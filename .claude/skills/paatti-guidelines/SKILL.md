---
name: paatti-guidelines
description: Project rules for Paatti (scam-baiting honeypot). Use when changing the persona, extractor, validators, read-back logic, report, or the live dashboard.
---

# Paatti project guidelines

## What must stay true
1. **Evidence is verbatim.** Every intel field carries a quote that is an exact substring of a SCAMMER turn. `CallSession._valid` enforces this; never loosen it (it is what blocks hallucinated LLM identifiers).
2. **Read-backs are generated in code, never by the LLM.** `CallSession.readback_line` repeats the captured digits so the scammer confirms them. Only a read-back followed by an affirmative (or the same value repeated) sets `confirmed`.
3. **Unconfirmed identifiers never reach `ncrp_fields`.** They stay in `identifiers` with `confirmed_by_readback: false`.
4. **The call never dies on an LLM failure.** Persona falls back to scripted lines; the rule extractor always runs.
5. **Claimed agency comes from self-identification.** A later "RBI safe account" must not overwrite "This is CBI".

## Workflow (everything-claude-code)
- New behaviour: `/tdd` — test in `backend/tests/` first, watch it fail, implement, keep coverage >= 80%.
- Extraction or persona change: add or extend a script in `evals/scripts/` with ground truth, then `/eval` (`python evals/run_evals.py`). Targets: key-field accuracy >= 90%, read-back gate PASS, p95 turn latency < 3 s.
- Before commit: `/verify` (pytest, vitest, frontend build) and the **security-reviewer** agent for anything touching input handling or the WebSocket.

## Map
| Concern | File |
|---|---|
| Schema | `backend/paatti/schema.py` |
| Format checks, spoken digits | `backend/paatti/validators.py` |
| Regex extractor, stage cues | `backend/paatti/rule_extractor.py` |
| Session, read-back, repeat registry | `backend/paatti/session.py` |
| Persona (Claude + script) | `backend/paatti/persona.py` |
| LLM extractor (structured outputs) | `backend/paatti/extractor.py` |
| Report JSON/PDF | `backend/paatti/report.py` |
| Sarvam STT/TTS | `backend/paatti/sarvam.py` |
| API + WebSocket | `backend/paatti/main.py` |
| Dashboard | `frontend/src/` |
