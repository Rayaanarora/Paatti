# Paatti

**Daisy wastes scammers' time. Paatti makes them hand over the evidence.**

Paatti is an AI honeypot for India's "digital arrest" scams. Kamala Paatti, a 72-year-old Tamil-speaking grandmother, answers the scammer's call, plays along, asks for the account number "one digit at a time, kanna", and **reads it back so the scammer confirms it**. Every identifier is captured with a verbatim quote as evidence and turned into a **Mule Intel Report** that a bank fraud desk or 1930 can act on inside the golden hour, before the next victim pays.

Built for BeyondHack 2.0 (SRM IST).

| Doc | What's in it |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | **Everything in one file**: problem, architecture, APIs, config, deployment state, verified results, limits, next steps |
| [docs/STUDY.md](docs/STUDY.md) | Full walkthrough: problem, every component, safety, glossary |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, data flow, deployment diagrams |
| [docs/DATABASE.md](docs/DATABASE.md) | SQLite schema and ER diagram |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Run locally, tunnel, Render, Twilio phone line |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | **The live demo script**: eight scammer lines, Paatti's replies, what to point at |
| [docs/ROUNDS.md](docs/ROUNDS.md) | Round-by-round checklist, 5-minute script, judge Q&A |

## Start everything with one click

Needs [Python 3.11+](https://www.python.org/downloads/) and [Node.js LTS](https://nodejs.org/).

- **Windows:** double-click `start.bat`
- **Mac / Linux:** run `./start.sh`

It installs everything, starts Paatti, creates a public HTTPS link (so the phone mic works) and opens it in your browser. Open that link on the laptop, click **Phone call via QR**, and scan with the phone. Keep the window open during the demo. Optional keys for AI replies and real voice go in `.env` (created on the first run).

## Personas

Twelve grandparents, each with their own language, region, backstory and voice, so a repeat caller never meets the same person twice. Pick one on the dashboard, or choose **Random** (or set `PAATTI_PERSONA=random`) for a different one every call.

| Key | Persona | Language | From |
|---|---|---|---|
| `paatti` | Kamala Paatti | Tamil | Mylapore, Chennai |
| `thatha` | Rangasamy Thatha (male) | Kongu Tamil | Coimbatore |
| `dadi` | Sharda Dadi | Hindi | Lucknow |
| `dadaji` | Ramesh Chand Dadaji (male) | Hindi, Marwari touch | Jaipur |
| `ammamma` | Lakshmi Ammamma | Telugu | Vijayawada |
| `ajji` | Shantamma Ajji | Kannada | Mysuru |
| `ammachi` | Mariamma Ammachi | Malayalam | Kottayam |
| `thakuma` | Bani Thakuma | Bengali | Kolkata |
| `aaji` | Sulabha Aaji | Marathi | Pune |
| `ba` | Hansaben Ba | Gujarati | Ahmedabad |
| `bauji` | Harbhajan Singh Bauji (male) | Punjabi | Ludhiana |
| `uncle` | Joseph Uncle (male) | Goan English | Margao, Goa |

`python evals/run_evals.py --persona all` runs every scam script against every persona.

## Features

| | |
|---|---|
| 📞 **Real phone calls** | Twilio number: dial in, or the dashboard rings an allowlisted phone. Paatti talks in a Tamil voice. |
| 📱 **Browser phone (QR)** | The judge scans a QR code; their phone shows a realistic call screen and becomes the scammer's phone. |
| 🧓 **Persona pack** | Tamil Kamala Paatti (default) and Hindi Sharda Dadi, injection-hardened, with an output guard |
| 🔁 **Read-back gate** | Digits are repeated back (generated in code, never by the AI); identifiers lock only after the caller confirms |
| 🔎 **Two extractors, one gate** | Instant regex rules plus Gemini JSON extraction; format validators plus a verbatim-quote check block hallucinations |
| 🖥️ **Live dashboard** | Transcript, four-stage tracker, intel cards that lock, time-wasted counter, live call list that auto-follows phone calls, any number of screens |
| 📄 **Mule Intel Report** | PDF and JSON: evidence quotes, corroboration, BNS 318/319, SHA-256 audio hash, NCRP-ready fields, "lead, not verdict" status |
| 🚨 **Dispatch** | Mock hand-off to a bank fraud desk or 1930 with a golden-hour countdown |
| 🗄️ **SQLite database** | Calls, transcripts, intel, stages, dispatches, and a cross-call mule registry (repeat match) |
| ⚡ **Never hits the limit** | Gemini model and key rotation, local rate limiting, prepared in-character replies, reply cache, scripted fallback |

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, WebSockets, Web Audio (AudioWorklet), `qrcode` |
| Backend | Python, FastAPI, uvicorn, Pydantic |
| AI | Google Gemini via `google-genai` (persona + extraction); Claude optional |
| Speech | Sarvam AI: Saaras (speech-to-text), Bulbul (Tamil/Hindi text-to-speech) |
| Telephony | Twilio Programmable Voice + bidirectional Media Streams |
| Database | SQLite (`sqlite3`) |
| Report | ReportLab PDF, JSON |
| Deploy | Docker, Render blueprint, or laptop + cloudflared/ngrok tunnel |

## Run it

```bash
cd frontend
npm install
npm run build
```

```bash
cd backend
pip install -e '.[dev]'
```

```bash
export GEMINI_API_KEY=your-gemini-key
export SARVAM_API_KEY=your-sarvam-key
cd backend
uvicorn paatti.main:app --port 8000
```

Open http://localhost:8000.
- **Dashboard demo:** Start call → open "Demo script lines" → watch the account card lock.
- **Phone demo:** open the dashboard via an HTTPS tunnel, click "Phone call via QR", scan it with a phone.
- **Real phone line:** see [docs/DEPLOY.md](docs/DEPLOY.md#real-phone-calls-twilio).

**No keys?** It still runs: scripted persona, rule extractor, browser speech.

| Keys set | Persona | Extractor | Voice |
|---|---|---|---|
| none | scripted + prepared replies | rules | browser speech / text |
| `GEMINI_API_KEY` | Gemini (model chain) | rules + Gemini | same |
| `SARVAM_API_KEY` | same | same | Sarvam Tamil/Hindi voice; phone channels |
| Twilio vars | same | same | real phone number |

`GET /api/health` shows active modes, where replies came from (read-back, intent, cache, LLM, script) and the Gemini pool's state.

## Tests and evals

```bash
cd backend && pytest --cov
cd frontend && npm test
python evals/run_evals.py
python evals/run_evals.py --llm --runs 3
python evals/redteam.py
```

`--llm` uses your Gemini key.

**Evals:** 8 scripted digital-arrest calls with ground truth. Five are realistic scams (CBI/Aadhaar, FedEx parcel, TRAI SIM with spoken digits and a correction, ED in Tamil-English, a customs call that hangs up before confirming). Three are adversarial (prompt injection, OTP and card requests, a suspicious caller).

**Current offline result:**
- **Checks:** 88/88 passed.
- **Account / IFSC / UPI accuracy:** 100%.
- **Read-back gate:** pass; no unconfirmed identifier reached the NCRP fields.
- **Persona:** stayed in character on every turn.

These are scripts we wrote; the live demo is the unscripted test. The `--llm` path has not been run against the real API.

**Backend:** 223 tests. The tests run without network access: Gemini, Sarvam and Twilio are faked.

## Safety by design
- **No invented identifiers:** an identifier is accepted only if its quote appears verbatim in what the caller said.
- **No unconfirmed data in NCRP fields:** unconfirmed identifiers never reach them.
- **Leads, not verdicts:** the report flags framing risk (a scammer could read out an innocent person's account), shows corroboration, and requires analyst review. Nothing is frozen automatically.
- **Can't be talked out of character:** three layers, so a scammer can't unmask her or turn her against us.
  1. **Nothing to leak:** her instructions are written entirely in her world (a frightened grandmother on the phone). They contain no words like AI, honeypot, police or report, so even a perfect leak gives nothing away.
  2. **Caught before the model:** prompt injection, "are you an AI?" (in 8 Indian languages), quizzes and Turing tests, and OTP requests are spotted in code. She answers with a confused, in-character line, and Gemini never sees the attack.
  3. **Output guard:** every model reply is checked before it is spoken. Any mention of AI, models, prompts or scams, any assistant-style phrasing, links, lists, trivia answers, or digits the caller never said gets replaced with a prepared line.
- **Red-team tested:** `python evals/redteam.py` sends 62 attacks to all 12 personas. Result: 744/744 stayed in character, 27/27 bad replies blocked, 0 false positives on normal speech. `--live 12` repeats the test against real Gemini with layer 2 switched off; see `evals/redteam/REPORT.md`.
- **Phone line security:**
  - **Signed webhooks:** every Twilio webhook must carry a valid signature.
  - **One-time stream tokens:** each call's audio stream needs a single-use token.
  - **Replay protection:** a replayed webhook is rejected.
  - **Locked-down outbound calls:** they need an operator token and an allowlisted number.
- **Dashboard limits:** live phone calls are watch-only, and caller IDs are masked in public listings.
- **Abuse limits:** session rate limits, turns per call, a bounded audio queue, and daily caps on Gemini, Sarvam and Claude.

## Limits and ethics
- **Prototype scope:** the dashboard has no login, and reports and dispatch are open to anyone with the link. Use an operator token (`PAATTI_OPERATOR_TOKEN`) and a private deployment for anything beyond a demo.
- **Mock dispatch:** it records what would be sent and contacts no one. The NCRP field layout is a proposed format.
- **Untested integrations:** Sarvam and Twilio message formats were written from their public APIs and tested with fakes; make one live test call before the demo.
- **Ethics:** our own line only, no real person's voice or data, legal review before any pilot.

## Development workflow
This repo uses [everything-claude-code](https://github.com/alphachoi/everything-claude-code) agents, skills, commands and rules (`.claude/`); see [CLAUDE.md](CLAUDE.md).
