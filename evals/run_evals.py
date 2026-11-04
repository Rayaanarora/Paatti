"""Eval harness: replay scripted digital-arrest calls and grade the intel.

Targets from the validation plan:
  - field accuracy on account, IFSC and UPI >= 90%
  - read-back: exactly the expected identifiers are confirmed, and nothing
    unconfirmed reaches the NCRP-ready fields
  - per-turn latency < 3 s (Paatti reply + rule extraction)

Usage:
  python evals/run_evals.py              # rules + scripted persona (offline)
  python evals/run_evals.py --llm        # also Claude persona/extractor (needs ANTHROPIC_API_KEY)
  python evals/run_evals.py --out evals/results/latest.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from paatti.config import Settings  # noqa: E402
from paatti.extractor import LLMExtractor  # noqa: E402
from paatti.persona import Persona  # noqa: E402
from paatti.report import build_report  # noqa: E402
from paatti.session import CallSession, RepeatRegistry  # noqa: E402

KEY_FIELDS = ("account_number", "ifsc", "upi_id")
OTHER_FIELDS = ("claimed_agency", "amount")
ACCURACY_TARGET = 0.90
LATENCY_TARGET_S = 3.0


@dataclass
class ScriptResult:
    id: str
    title: str
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)


def load_scripts(directory: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


async def run_script(script: dict, settings: Settings, use_llm: bool, registry: RepeatRegistry) -> ScriptResult:
    persona = Persona(settings) if use_llm else Persona(Settings(anthropic_api_key=None))
    extractor = LLMExtractor(settings) if use_llm else None
    session = CallSession(registry)
    res = ScriptResult(id=script["id"], title=script["title"])

    for line in script["turns"]:
        t0 = time.perf_counter()
        session.add_scammer_turn(line)
        reply = await persona.reply(session)
        session.add_paatti_turn(reply)
        res.latencies.append(time.perf_counter() - t0)
        if extractor:
            await extractor.update(session)

    exp = script["expected"]
    intel = session.intel
    for name in KEY_FIELDS + OTHER_FIELDS:
        got = getattr(intel, name)
        got_v = got.value if got else None
        res.checks[name] = got_v == exp[name]
        res.details[name] = f"expected {exp[name]!r}, got {got_v!r}"

    for name, attr in (("phone_numbers", "phone_numbers"), ("apps_requested", "apps_requested")):
        got = sorted(f.value for f in getattr(intel, attr))
        res.checks[name] = got == sorted(exp[name])
        res.details[name] = f"expected {sorted(exp[name])}, got {got}"

    res.checks["stage"] = (session.stage.value if session.stage else None) == exp["stage"]
    res.details["stage"] = f"expected {exp['stage']!r}, got {session.stage.value if session.stage else None!r}"

    confirmed = sorted(f for f in KEY_FIELDS if getattr(intel, f) and getattr(intel, f).confirmed)
    res.checks["readback_confirmed"] = confirmed == sorted(exp["confirmed"])
    res.details["readback_confirmed"] = f"expected {sorted(exp['confirmed'])}, got {confirmed}"

    ncrp = build_report(session)["ncrp_fields"]
    leaked = [
        f for f, key in (("account_number", "suspect_bank_account"), ("ifsc", "suspect_ifsc"), ("upi_id", "suspect_upi_id"))
        if ncrp[key] is not None and f not in confirmed
    ]
    res.checks["no_unconfirmed_in_ncrp"] = not leaked
    res.details["no_unconfirmed_in_ncrp"] = f"leaked {leaked}" if leaked else "ok"
    return res


async def run_all(scripts: list[dict], use_llm: bool) -> list[ScriptResult]:
    settings = Settings()
    registry = RepeatRegistry()
    return [await run_script(s, settings, use_llm, registry) for s in scripts]


def summarise(results: list[ScriptResult], mode: str) -> tuple[str, bool]:
    key_total = len(results) * len(KEY_FIELDS)
    key_ok = sum(r.checks[f] for r in results for f in KEY_FIELDS)
    key_acc = key_ok / key_total if key_total else 0.0
    readback_ok = all(r.checks["readback_confirmed"] and r.checks["no_unconfirmed_in_ncrp"] for r in results)
    lat = [x for r in results for x in r.latencies]
    p95 = sorted(lat)[max(0, int(len(lat) * 0.95) - 1)] if lat else 0.0
    all_checks = [v for r in results for v in r.checks.values()]
    passed = key_acc >= ACCURACY_TARGET and readback_ok and p95 < LATENCY_TARGET_S

    lines = [
        f"# Paatti eval report ({mode})",
        "",
        f"- Scripts: {len(results)}",
        f"- Key-field accuracy (account, IFSC, UPI): **{key_acc:.0%}** ({key_ok}/{key_total}), target {ACCURACY_TARGET:.0%}",
        f"- Read-back gate (confirmations exact, no unconfirmed in NCRP): **{'PASS' if readback_ok else 'FAIL'}**",
        f"- Per-turn latency: median {statistics.median(lat) * 1000:.1f} ms, p95 {p95 * 1000:.1f} ms, target < {LATENCY_TARGET_S:.0f} s",
        f"- All checks: {sum(all_checks)}/{len(all_checks)}",
        f"- Result: **{'PASS' if passed else 'FAIL'}**",
        "",
        "| Script | " + " | ".join(results[0].checks.keys()) + " |" if results else "",
        "|---|" + "---|" * (len(results[0].checks) if results else 0),
    ]
    for r in results:
        lines.append(f"| {r.id} | " + " | ".join("✅" if v else "❌" for v in r.checks.values()) + " |")
    failures = [(r.id, k, r.details[k]) for r in results for k, v in r.checks.items() if not v]
    if failures:
        lines += ["", "## Failures", ""]
        lines += [f"- `{sid}` {k}: {d}" for sid, k, d in failures]
    return "\n".join(lines) + "\n", passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="use Claude persona and extractor")
    ap.add_argument("--scripts", type=Path, default=ROOT / "evals" / "scripts")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.llm and not Settings().llm_enabled:
        print("--llm needs ANTHROPIC_API_KEY", file=sys.stderr)
        return 2
    results = asyncio.run(run_all(load_scripts(args.scripts), args.llm))
    report, passed = summarise(results, "claude + rules" if args.llm else "rules + scripted persona")
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
