# Paatti

**Daisy wastes scammers' time. Paatti makes them hand over the evidence.**

Paatti is a honeypot for Indian "digital arrest" scams. A 72-year-old Tamil-speaking grandmother persona keeps the fake CBI/police officer on the line, asks for account digits one at a time and reads them back so the scammer confirms them, and turns the call into a **Mule Intel Report** that a bank fraud team or cyber cell can act on before the next victim pays.

Built for BeyondHack 2.

## What it does

| Feature | Where |
|---|---|
| **Paatti persona**: Claude with a stalling, code-mixed Tamil-English prompt; scripted fallback when the LLM is slow or unavailable | `backend/paatti/persona.py` |
| **Read-back**: Paatti repeats captured digits (generated in code, never by the LLM); the identifier locks only after the caller confirms | `backend/paatti/session.py` |
| **Live intel extractor**: a deterministic regex pass on every turn plus Claude structured outputs over the rolling transcript; every field carries a timestamped verbatim quote | `rule_extractor.py`, `extractor.py` |
| **Validators**: IFSC and UPI formats, account length, Indian mobile numbers, spoken digits ("double five", "onnu rendu") | `validators.py` |
| **Operator dashboard**: live transcript, four-stage tracker (hook, fear, isolation, extraction), intel cards that lock on read-back, time-wasted counter | `frontend/` |
| **Mule Intel Report**: one-click PDF and JSON with identifiers, confidence, evidence quotes, stage timeline, BNS 318–319 (plus IT Act 66D, marked as suggested), SHA-256 audio and transcript hashes, and NCRP-ready fields | `report.py` |
| **Repeat match** (stretch goal): flags an account, UPI ID or phone seen in an earlier call and raises its confidence | `RepeatRegistry` in `session.py` |

### Safety properties
- An LLM finding is rejected unless its quote appears verbatim in the scammer turn it cites, which blocks hallucinated identifiers.
- Identifiers that were never confirmed by read-back are kept out of `ncrp_fields`.
- The caller's own claim ("This is CBI") sets the claimed agency; a later "RBI safe account" doesn't overwrite it.

## Run it

```bash
cd backend
pip install -e '.[dev]'
cd ../frontend
npm install
npm run build
cd ../backend
uvicorn paatti.main:app --port 8000
```

Open http://localhost:8000, click **Start call**, then type or speak as the scammer. The **Demo script lines** drawer plays a full call with one click per line.

To develop the UI with hot reload, run `npm run dev` in `frontend/` (it proxies `/api` and `/ws` to :8000).

### Modes
Everything works offline; keys switch on the AI services (see `.env.example`).

| Env var set | Persona | Extractor | Voice |
|---|---|---|---|
| none | scripted | rules | browser Web Speech (Chrome) or text |
| `ANTHROPIC_API_KEY` | Claude (`claude-opus-5-5`, low effort) | rules + Claude | same |
| `SARVAM_API_KEY` | same | same | Saaras v3 STT and Bulbul v3 TTS, push-to-talk |

`GET /api/health` reports the active modes. Claude calls use server-side refusal fallbacks (`fallbacks: "default"`) and fall back to the script on any API error, so the call never drops.

## Tests and evals

```bash
cd backend && pytest --cov
cd frontend && npm test
python evals/run_evals.py
python evals/run_evals.py --llm
```

`--llm` needs `ANTHROPIC_API_KEY`.

The eval replays the five scripted digital-arrest calls in `evals/scripts/` (CBI/Aadhaar, FedEx parcel, TRAI SIM with spoken digits and a correction, ED in Tanglish, and a customs call that hangs up before confirming). It grades them against the validation plan:

- key-field accuracy (account, IFSC, UPI) of at least 90%
- read-back gate: exactly the expected identifiers are confirmed, and none of the unconfirmed ones leak into the NCRP fields
- p95 per-turn latency under 3 s

Current offline result: 100% key-field accuracy (15/15), 50/50 checks, read-back gate PASS. The `--llm` path has not been run against the real API yet.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/sessions` | start a call (rate limited) |
| POST | `/api/sessions/{id}/turn` | text-mode turn `{text}` → Paatti reply and state |
| POST | `/api/sessions/{id}/end` | end the call |
| GET | `/api/sessions/{id}/report.json` / `.pdf` | Mule Intel Report |
| WS | `/ws/sessions/{id}` | live call: `scammer_text`, `audio_utterance` (Sarvam), `audio_chunk` (hash only), `tick`, `end` |

## Development workflow

The repo vendors [everything-claude-code](https://github.com/alphachoi/everything-claude-code) agents, skills, commands and rules under `.claude/`. See `CLAUDE.md`.

## Limits and ethics
- Scammers may give throwaway accounts; repeat matches across calls raise confidence.
- The NCRP / Suspect Registry export is a proposed format, not a live integration.
- Use only on your own line, with no real person's voice or data; get a legal review before any pilot.
- Sarvam endpoint fields follow its public REST API and are configurable via env; check them against your account before the demo.
