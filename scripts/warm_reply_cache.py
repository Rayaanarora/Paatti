"""Pre-generate Paatti's replies for rehearsed lines so the demo needs no API calls.

Plays the dashboard demo lines and selected eval scripts through the real persona
(Gemini) once and stores every reply in backend/paatti/data/reply_cache.json.
Replaying the same lines later is answered from that file.

  GEMINI_API_KEY=... python scripts/warm_reply_cache.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from paatti.config import Settings  # noqa: E402
from paatti.persona import Persona  # noqa: E402
from paatti.session import CallSession, RepeatRegistry  # noqa: E402


def demo_lines() -> list[str]:
    """The exact lines behind the dashboard's 'Demo script lines' buttons."""
    src = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    block = src.split("const DEMO_LINES = [", 1)[1].split("]", 1)[0]
    return re.findall(r"'((?:[^'\\]|\\.)*)'", block)


async def play(persona: Persona, lines: list[str], key: str) -> None:
    s = CallSession(RepeatRegistry(), persona_key=key)
    for line in lines:
        s.add_scammer_turn(line)
        s.add_paatti_turn(await persona.reply(s))


async def main() -> int:
    settings = Settings()
    if settings.llm_provider != "gemini":
        print("Set GEMINI_API_KEY first.", file=sys.stderr)
        return 2
    persona = Persona(settings)
    before = len(persona.cache)
    scripts = ["01_cbi_aadhaar", "02_fedex_parcel_upi", "04_ed_tanglish"]
    sets = [("demo", demo_lines(), "paatti")] + [
        (name, json.loads((ROOT / "evals" / "scripts" / f"{name}.json").read_text())["turns"], "paatti")
        for name in scripts
    ]
    for name, lines, key in sets:
        await play(persona, lines, key)
        print(f"{name}: {len(lines)} lines")
    print(f"reply sources: {persona.stats}")
    print(f"cache entries: {before} -> {len(persona.cache)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
