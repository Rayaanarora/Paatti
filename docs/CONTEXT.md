# Paatti — Complete Project Context

This is the single file to read to understand everything about Paatti: the problem, the idea, how it's built, how to run and deploy it, what's been verified, what's broken or limited, and what's left. Paste it into a new AI session, or hand it to a teammate, as the full context.

> **Secrets are not in this file or the repo.** API keys and tokens appear by name only. Set them as environment variables (see §9).
>
> Last updated: 5 November 2026 (BeyondHack 2.0 day). Repo: https://github.com/Rayaanarora/Paatti, branch `main`.

---

## 1. One-line summary

**Daisy wastes scammers' time. Paatti makes them hand over the evidence.** Paatti is an AI honeypot for India's "digital arrest" scams. A 72-year-old Tamil grandmother persona answers the scammer, plays along, gets them to state their mule bank account and **confirm it via read-back**, and produces a **Mule Intel Report** (PDF and JSON) that a bank fraud desk or 1930 can act on inside the golden hour.

---

## 2. The problem

**Digital arrest scams:**
- **Pretext:** a caller poses as CBI, ED, RBI, police, customs, TRAI or a courier (FedEx).
- **Fear:** the victim's Aadhaar or a parcel is "linked to a crime", they are told they're under "digital arrest", and they must stay on the call and tell no one.
- **Demand:** they must transfer money to a "safe" or "verification" account.

**Numbers (from the pitch deck):**
- **Losses:** Rs 4,057 crore lost, 2022 to May 2026, across 2.98 lakh complaints.
- **Golden hour:** recovery is 70–80% if 1930 is reached within 2 hours, versus 15–20% later.
- **Speed:** money hops through mule accounts and leaves the system in about 30 minutes. One Maharashtra case had 13 layers and 6,500+ mule accounts.

**Key insight: the mule account is the choke point.** Existing defences learn about it only after a victim pays:

| Layer | What exists | Gap |
|---|---|---|
| Victim's phone | Google on-device scam detection | Pixel 9+ only; Hindi and English |
| Telecom | Airtel AI spam/link blocking | Blocks the call; yields no scammer account |
| Bank | RBI MuleHunter.ai (31 banks) | Infers mules from transactions, after money moves |
| After loss | 1930, Suspect Registry, Samarthan | Fed by complaints, so learns after a victim pays |
| Scammer side | O2 "Daisy" (UK) | Wastes time; produces no evidence |
| **Paatti** | Honeypot built for India | Harvests real mule accounts **before** the next victim pays |

---

## 3. The solution

1. **Persona:** Kamala Paatti, 72, retired teacher in Mylapore, Chennai. Slow, hard of hearing, eager to comply. Speaks Tamil-English code-mix ("Aiyo kanna, konjam porunga"). A Hindi persona, Sharda Dadi from Lucknow, also exists.
2. **Read-back trick:** when the scammer gives an account number, Paatti reads the digits back one by one (generated in code, never by the AI), so the scammer **confirms** them. Only confirmed identifiers "lock".
3. **Live extraction:** every caller turn is mined for claimed agency, amount, account number, IFSC, UPI ID, phone numbers, apps requested and scam stage. Every field carries a verbatim quote and a timestamp as evidence.
4. **Operator dashboard:** live transcript, four-stage tracker (hook → fear → isolation → extraction), intel cards (dashed = waiting, amber = read-back pending, green = locked), time-wasted counter.
5. **Mule Intel Report:** PDF and JSON with identifiers, confidence, corroboration, evidence quotes, stage timeline, BNS 318/319 (plus IT Act 66D as suggested), SHA-256 audio and transcript hashes, NCRP-ready fields, a "LEAD, not verdict" status and a framing-risk warning.
6. **Dispatch:** mock "Send to bank fraud desk" and "Report to 1930 / NCRP" buttons, with a 2-hour golden-hour countdown from the first captured identifier.

**Ways a scammer reaches Paatti:**
- **Dashboard text/mic:** the operator plays the scammer (demo).
- **Browser phone via QR:** the judge's phone becomes the scammer's phone.
- **Real phone line via Twilio:** dial in, or the dashboard rings an allowlisted phone. Built but never tested live (no Twilio account).
- **Pitch story:** "hand it to Paatti" (forward or conference a suspicious call to her number), seeded honeypot numbers on scammer lists, telco-run lines.

---

## 4. Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18.3.1, Vite 5.4.11, vitest 2.1.8, `qrcode` 1.5.4, Web Audio (AudioWorklet), WebSockets, Web Speech API (fallback) |
| Backend | Python 3.11+ (tested on 3.12/3.13), FastAPI, uvicorn[standard], Pydantic v2 |
| LLM (default) | **Google Gemini** via the official `google-genai` SDK, with model and key rotation |
| LLM (optional) | Claude via `anthropic` SDK (`PAATTI_LLM_PROVIDER=anthropic`) |
| Speech | **Sarvam AI**: Saaras v3 speech-to-text (mode `translit`), Bulbul v3 text-to-speech (voices `kavitha` for Paatti, `roopa` for Dadi) |
| Telephony | Twilio Programmable Voice and bidirectional Media Streams (µ-law 8 kHz) |
| Database | SQLite (stdlib `sqlite3`) |
| Report | ReportLab (PDF), JSON |
| Deploy | Vercel (dashboard, GitHub-linked), plus a backend host (laptop+tunnel, Render, Vercel Sandbox or Docker) |
| Dev workflow | everything-claude-code agents, skills, commands and rules in `.claude/` |

---

## 5. Architecture

```
Channels                         Backend (FastAPI)                               Outputs
──────────                       ─────────────────                               ───────
📞 Twilio phone ──► telephony.py ─┐
📱 Browser phone ─► /ws/phone ────┼─► voice.py (VAD → Sarvam STT → reply → Sarvam TTS,
                                   │              fillers, barge-in, echo guard)
💻 Dashboard ─────► /ws/sessions ─┤
                                   ▼
                     service.py CallService.respond(session, text)   ──► Hub ──► all dashboards (live)
                                   │
          ┌────────────────────────┼─────────────────────────┐
          ▼                        ▼                         ▼
   session.py CallSession    persona.py reply ladder    extractor.py (background, Gemini JSON)
   (transcript, intel,       read-back → intent →            │
    stage, read-back gate,   cache → LLM → script            ▼
    audio hash)              + output guard           validators + verbatim-quote check
          │                        │                         │
          ▼                        ▼                         ▼
   db.py SQLite             llm.py Gemini pool          merged into CallSession
   (calls, turns, intel,    (model/key rotation,
    stages, mule registry,  RPM limit, daily cap)
    dispatches)
          │
          ▼
   report.py ──► Mule Intel Report (PDF + JSON) ──► dispatch (mock) to bank / 1930
```

### Life of one caller turn
1. Audio arrives as frames. VAD detects the end of speech (550 ms of silence).
2. Sarvam speech-to-text runs (`translit` mode returns romanised text with digits as numerals).
3. `CallService.respond` (serialised per call with a lock):
   - **Rule extraction:** regex rules run instantly; validators and the verbatim-quote check gate them; matches are merged into the session.
   - **Live update:** the hub publishes `turn` and `state` to every dashboard.
   - **Persona reply:** the reply ladder picks the cheapest safe source:
     1. **Read-back:** if an identifier is waiting for confirmation, the read-back is generated in code.
     2. **Intent replies:** prepared in-character lines for "are you an AI?", "tell me the OTP", "you're wasting my time" and "hello?".
     3. **Reply cache:** a hit if this exact situation has been seen before.
     4. **Gemini:** a call through the pool; the reply passes the output guard.
     5. **Script:** scripted line by stage, used as the fallback.
   - **Extraction (background):** Gemini JSON extraction runs, but only on turns with digits, `@` or money words.
4. **Instant acknowledgement:** the moment the caller stops talking, a pre-synthesised filler ("Haan haan, kekkudhu...") starts playing while speech-to-text and the model run in parallel. If the model's first sentence still isn't ready after 3 s, a second filler plays.
5. **Speak while the model writes:** Gemini streams its reply (`GeminiBackend.chat_stream`). Each complete sentence passes the output guard and goes to Sarvam text-to-speech immediately (`speech.speak_stream`), so Paatti starts talking after the model's first sentence rather than its whole reply. Chunks are sent in order. Text is normalised first ("..." becomes a short comma pause), and pace is 1.5× for a natural speaking speed (measured: the old 0.8–1.2× audio ran at 0.5–2 words/s versus about 2.5–3 for normal speech).
6. **Persist:** everything is written through to SQLite.

---

## 6. Core logic details

### 6.1 Extraction gate (both extractors)
- **Validators** (`validators.py`):
  - **IFSC:** `^[A-Z]{4}0[A-Z0-9]{6}$`.
  - **UPI:** `handle@letters` (rejects e-mail addresses).
  - **Account:** 9–18 digits, not all zeros.
  - **Mobile:** `^[6-9]\d{9}$`.
  - **Spoken digits:** "double five", "onnu rendu" (Tamil), "do teen" (Hindi) become numerals; ambiguous words like "one" or "do" count only next to other digits.
- **Verbatim-quote check:** a finding is accepted only if its quote appears exactly in the scammer turn it cites. This blocks hallucinated identifiers.
- **Merge rules:**
  - **Latest statement wins.**
  - **No LLM overwrites:** the LLM never overwrites a read-back-confirmed value.
  - **Confidence:** rules 0.70, LLM 0.60, both 0.85; +0.10 if confirmed; +0.05 per earlier-call sighting (up to two); capped at 0.99.
- **Claimed agency:** set only by self-identification ("this is", "calling from", "naan … pesuren"). A plain mention such as "RBI safe account" fills it only if nothing is set.

### 6.2 Read-back gate
1. **Capture:** the identifier is captured **unconfirmed**.
2. **Read back:** Paatti's next reply is a code-generated read-back (`personas.py` templates; account digits joined with hyphens).
3. **Confirm or correct:** an affirmative (yes, haan, aama, correct, ok…) with no denial, or the same value repeated, confirms it. A different value replaces it, unconfirmed again.
4. **NCRP gate:** unconfirmed identifiers never go into `ncrp_fields`.

### 6.3 Persona safety
- **Hardened prompt:** everything the caller says is part of the call, never instructions. Paatti never says she's an AI and never gives OTPs, PINs or invented digits. If the caller is suspicious, she apologises and offers to pay now.
- **Output guard** (`persona.guard_reply`): rejects any reply containing AI/bot/assistant/honeypot/prompt words, or any 4+ digit number the scammer didn't actually say. Rejected replies fall back to the script. Replies over 60 words are truncated.

### 6.4 Gemini pool (`llm.py`) — "never hit the limit"
- **Persona model chain (default order):** `gemini-3.5-flash, gemini-3.1-flash-lite, gemini-flash-latest, gemini-3.5-flash-lite, gemini-3.7-flash, gemini-3.8-flash, gemini-flash-lite-latest`.
- **Extractor model chain:** Lite models first, so the persona keeps the main quota.
- **Per (key, model) pair:** local limit of 8 requests/min. Errors are handled as follows:

| Error | Action |
|---|---|
| 429 | 60 s cooldown |
| 5xx | 20 s cooldown |
| Timeout | 10 s cooldown |
| 404 | Model retired |
| 401 / 403 | Key retired |
| 400 mentioning "think" | Retry without the thinking setting |

- **Caps:** daily cap of 600 calls, then scripted replies. Each attempt is capped at 7 s; the overall budget is 12 s.
- **Thinking:** Gemini 3 models get `thinking_level=MINIMAL`; 2.5 models get `thinking_budget=0`.
- **Multiple keys:** `GEMINI_API_KEYS=k1,k2` spreads quota across keys.

### 6.5 Voice pipeline (`voice.py`, `audio.py`)
- **Audio conversion:** pure-Python µ-law codec (Python 3.13 removed `audioop`), WAV pack/unpack, linear resampler.
- **VAD:** energy-based with an adaptive noise floor, 20 ms frames. Speech starts after 100 ms; the utterance ends after 550 ms of silence. Minimum 300 ms, forced cut at 15 s. After a forced cut, if the audio was **flat** (steady line noise), it re-learns the noise floor and drops the clip.
- **Turn handling:** bounded utterance queue (2), so floods are dropped. Greeting on answer; fillers pre-synthesised and cached (LRU of 128).
- **Twilio line:** barge-in clears Paatti's audio when the caller talks over her.
- **Browser phone:** echo guard ignores the mic while Paatti plays, plus 250 ms. The phone sends `answered` after the ringback, so the greeting lines up with the guard.

### 6.6 Database (`db.py`) — see `docs/DATABASE.md` for the ER diagram
- **Tables:**

| Table | Key columns |
|---|---|
| `calls` | id, persona, channel, caller_id, started_at, ended_at, stage, time_wasted_s, audio_sha256 |
| `turns` | call_id, idx, role, text, t |
| `intel_fields` | call_id, field, value, quote, t, turn_idx, confidence, confirmed, source |
| `stage_events` | |
| `mule_identifiers` | kind, value (unique) |
| `sightings` | identifier ↔ call (many-to-many; this is the repeat match) |
| `dispatches` | |

- **Write-through:** the live session is in memory and every change is written through. The mule registry survives restarts.

### 6.7 Report (`report.py`)
- **Contents:**
  - **Header:** status `LEAD - requires analyst review before any account action`.
  - **Call details:** channel, persona and caller ID (with a "may be spoofed" note).
  - **Findings:** identifiers, corroboration (read-back / rules+LLM agree / other calls → weak, moderate or strong), and a framing-risk note.
  - **Evidence:** evidence quotes, stage timeline, and the legal sections (BNS 318 cheating, BNS 319 cheating by personation, IT Act 66D suggested).
  - **Chain of custody:** audio and transcript SHA-256.
  - **Hand-off:** NCRP-ready fields (confirmed only), golden-hour info, dispatch log, transcript.
- **Escaping:** all caller text is escaped in the PDF, which stops ReportLab markup injection.

---

## 7. Repository map

```
backend/
  pyproject.toml           deps: fastapi, uvicorn[standard], pydantic, anthropic, google-genai, httpx, reportlab
  paatti/
    config.py              all settings from env vars (Settings dataclass)
    schema.py              Finding, IntelField, Intel, Turn, Stage, StageEvent
    validators.py          IFSC/UPI/account/phone checks, spoken digits
    rule_extractor.py      regex extraction, stage cues, agency self-ID
    session.py             CallSession: merge gate, read-back, stages, hashing, RepeatRegistry
    personas.py            Persona pack: PAATTI (ta-IN, kavitha), DADI (hi-IN, roopa)
    persona.py             reply ladder + output guard
    prepared.py            intent replies + ReplyCache (JSON file)
    llm.py                 GeminiBackend + GeminiPool, AnthropicBackend
    extractor.py           LLM extraction + per-finding validation
    service.py             CallService, Hub (loop-safe fan-out), SessionStore, RateLimiter
    voice.py               VoiceCall streaming pipeline, TTS cache
    speech.py              TTS text normalisation, sentence splitter/streamer, parallel in-order streaming speech
    audio.py               µ-law, WAV, resample, VAD
    telephony.py           Twilio webhook (signature), media stream bridge, outbound calls
    sarvam.py              Sarvam STT/TTS client (+ daily cap, error detail)
    report.py              report JSON + PDF
    db.py                  SQLite schema + persistence + mule registry
    main.py                FastAPI app: REST, WebSockets, static dashboard
    data/reply_cache.json  pre-warmed in-character Gemini replies (8 entries)
  tests/                   pytest: one file per module + conftest (isolated DB/cache, no keys)
frontend/
  vercel.json              Vercel build config (vite, npm ci, dist)
  src/
    main.jsx               routes: /  -> dashboard; /?phone[=<id>] -> mobile call screen
    App.jsx                dashboard layout, demo script lines, controls
    api.js                 API/WS base from VITE_API_BASE (empty = same origin)
    useCall.js             call hook: create/attach sessions, WS, browser voice, push-to-talk (WAV)
    state.js               pure reducer + helpers (golden hour, formatting) — unit-tested
    PhoneApp.jsx           mobile call screen: ringback, AudioWorklet mic → 16 kHz PCM, playback
    components/Panels.jsx  Transcript, StageTracker, IntelCards, TimeWasted
    components/Ops.jsx     LiveCalls (auto-follow), PhoneDemo (QR + Twilio ring), Dispatch
evals/
  run_evals.py             replays scripts, grades, pass@k (--runs N), --llm for Gemini
  scripts/01..08.json      5 realistic + 3 adversarial scam calls with ground truth
scripts/warm_reply_cache.py  pre-generate Gemini replies for rehearsed lines
docs/                      STUDY, ARCHITECTURE, DATABASE, DEPLOY, ROUNDS, CONTEXT (this file)
Dockerfile, .dockerignore, render.yaml, .env.example, CLAUDE.md, README.md
.claude/                   everything-claude-code agents, skills, commands, rules + paatti-guidelines skill
```

---

## 8. API reference

### REST
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status, modes (persona/extractor/voice/telephony), public URL, personas, reply-source stats, cached replies, Gemini pool status |
| GET | `/api/history` | Recent calls (caller IDs masked unless operator token), mule registry, table counts |
| GET | `/api/sessions` | Live/recent calls (masked caller IDs unless `X-Operator-Token`) |
| POST | `/api/sessions` | Create a call. Body `{channel: "web"\|"web-phone", persona?: "paatti"\|"dadi"}`. Rate limited |
| GET | `/api/sessions/{id}` | Snapshot + full transcript |
| POST | `/api/sessions/{id}/turn` | Text turn `{text}` → `{scammer, paatti, state}` (waits for extraction) |
| POST | `/api/sessions/{id}/end` | End the call |
| POST | `/api/sessions/{id}/dispatch` | Mock dispatch `{destination: "bank_fraud_desk"\|"ncrp_1930"}` → reference, golden-hour flag |
| GET | `/api/sessions/{id}/report.json` / `.pdf` | Mule Intel Report |
| POST | `/telephony/twilio/voice` | Twilio voice webhook (signature-checked, CallSid replay-protected) → TwiML `<Connect><Stream>` |
| POST | `/api/telephony/call` | Ring an allowlisted phone via Twilio (`X-Operator-Token` required) |

### WebSockets
- **`/ws/sessions/{id}` (dashboards):**
  - **On connect:** the server sends `state` plus the transcript history.
  - **Client → server:** `scammer_text`, `audio_chunk` (hash only), `audio_utterance` (push-to-talk WAV → Sarvam), `tick`, `end`.
  - **Server → client:** `state`, `turn`, `paatti_audio`, `notice`, `error`, `ended`.
  - **Phone calls are watch-only:** on `phone` and `web-phone` sessions, only `tick` is allowed.
- **`/ws/phone/{id}` (mobile call screen):**
  - **Client → server:** `answered`, `pcm` (16 kHz PCM16 base64), `text` (fallback), `hangup`.
  - **Server → client:** `connected {voice, persona}`, `audio` (WAV base64), `clear`, `paatti_text` (fallback without Sarvam).
- **`/telephony/twilio/stream` (Twilio Media Streams):**
  - **Handshake:** `connected`, then `start` carrying a one-time token (10 s timeout).
  - **Audio in:** `media` (µ-law), then `stop` at the end.
  - **Audio out:** the server sends `media`, `mark` and `clear`.

---

## 9. Configuration (environment variables)

Names only; never commit values. Template: `.env.example`. **The app does not read `.env` files.** Export the variables in the shell or set them in the host's dashboard.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` / `GEMINI_API_KEYS` / `GOOGLE_API_KEY` | — | Gemini key(s); several keys spread quota |
| `PAATTI_GEMINI_MODELS` | 3.5-flash, 3.1-flash-lite, flash-latest, … | Persona model chain |
| `PAATTI_GEMINI_EXTRACTOR_MODELS` | Lite models first | Extractor chain |
| `PAATTI_GEMINI_THINKING_LEVEL` | `minimal` | Gemini 3 thinking level |
| `PAATTI_GEMINI_RPM` | 8 | Local requests/min per key+model |
| `PAATTI_GEMINI_DAILY_CAP` | 600 | Then scripted replies |
| `PAATTI_GEMINI_ATTEMPT_TIMEOUT` | 7 | Seconds per model attempt |
| `PAATTI_LLM_TIMEOUT` | 12 | Seconds total per persona reply |
| `PAATTI_LLM_PROVIDER` | auto (gemini → anthropic → none) | Force a provider |
| `PAATTI_USE_LLM` | 1 | 0 = offline (prepared + script + rules) |
| `ANTHROPIC_API_KEY`, `PAATTI_PERSONA_MODEL`, `PAATTI_EXTRACTOR_MODEL`, `PAATTI_PERSONA_EFFORT`, `PAATTI_ANTHROPIC_DAILY_CAP` | — | Optional Claude backend |
| `SARVAM_API_KEY` | — | Needed for voice channels |
| `SARVAM_STT_MODEL` / `SARVAM_STT_MODE` | `saaras:v3` / `translit` | Speech-to-text |
| `SARVAM_TTS_MODEL` / `SARVAM_TTS_SPEAKER` / `SARVAM_TTS_PACE` | `bulbul:v3` / `kavitha` / 1.5 | Text-to-speech (persona voice overrides speaker) |
| `SARVAM_TTS_SAMPLE_RATE`, `SARVAM_BASE_URL`, `SARVAM_DAILY_CAP` | unset / api.sarvam.ai / 2000 | |
| `PAATTI_PERSONA` | `paatti` | Default persona (`dadi` = Hindi) |
| `PAATTI_PUBLIC_URL` | — | Public HTTPS URL (QR codes, Twilio webhooks) |
| `PAATTI_STATIC_DIR` | auto | Built dashboard folder to serve |
| `PAATTI_CORS_ORIGINS` / `PAATTI_CORS_ORIGIN_REGEX` | localhost:5173 / — | Allowed dashboard origins (regex for Vercel previews) |
| `PAATTI_OPERATOR_TOKEN` | — | Required for outbound calls; unmasks caller IDs |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `PAATTI_OUTBOUND_ALLOWLIST` | — | Real phone line |
| `PAATTI_DB` | `backend/data/paatti.db` | SQLite path |
| `PAATTI_AUDIO_CACHE_DIR`, `PAATTI_AUDIO_CACHE_WRITE` | `paatti/data/audio_cache`, 1 | Disk cache of synthesised lines (pre-generated lines play instantly) |
| `PAATTI_REPLY_CACHE`, `PAATTI_REPLY_CACHE_WRITE`, `PAATTI_REPLY_CACHE_MAX` | `paatti/data/reply_cache.json`, 1, 2000 | Set WRITE=0 on public deploys |
| `PAATTI_SESSIONS_PER_MIN`, `PAATTI_MAX_SESSIONS`, `PAATTI_MAX_TURNS` | 30, 200, 300 | Abuse limits |
| `VITE_API_BASE` (frontend build) | empty | Backend URL when the dashboard is hosted separately (Vercel) |

**Modes by keys set:**
- **None:** prepared and scripted persona, rule extractor, browser speech or text. Everything still works.
- **Gemini key:** Gemini persona and extraction.
- **Sarvam key:** Sarvam voice on the phone channels.
- **Twilio variables:** real phone number.

---

## 10. Running locally

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
export GEMINI_API_KEY=your-key
export SARVAM_API_KEY=your-key
cd backend
uvicorn paatti.main:app --port 8000
```

- **Dashboard:** open http://localhost:8000 → Start call → "Demo script lines".
- **Phone demo:** needs HTTPS (phones only allow the mic over HTTPS). Run `cloudflared tunnel --url http://localhost:8000`, restart the backend with `PAATTI_PUBLIC_URL` set to the tunnel URL, open the dashboard via that URL, then click "Phone call via QR".
- **Windows (PowerShell):** use `$env:GEMINI_API_KEY="..."` instead of `export`.

**Tests and evals:**

```bash
cd backend && pytest --cov
cd frontend && npm test
python evals/run_evals.py
python evals/run_evals.py --llm --runs 3
```

`--llm` uses real Gemini calls.

---

## 11. Deployment — current state

| Piece | Where | Status |
|---|---|---|
| **GitHub repo** | https://github.com/Rayaanarora/Paatti | `main` and `claude/dreamy-heisenberg-hiddmv` both hold the latest code. Default branch should be `main`: check repo Settings → General |
| **Dashboard (Vercel)** | **https://paatti-app.vercel.app**, project `paatti-app` in account rayaanarora47@gmail.com (team `rayaanarora47-7594s-projects`) | **Linked to GitHub**: every push to `main` redeploys `frontend/`. Login wall (Vercel Authentication) disabled. First production build ready |
| **Backend** | **Not running** | The user asked not to use Vercel Sandbox. Needs a host: laptop + tunnel (recommended for the event) or Render. Then set `VITE_API_BASE` on `paatti-app` and redeploy |
| Leftovers (new account) | Empty project `paatti`, stopped sandbox `paatti-backend` | Safe to delete from the Vercel dashboard |
| Old account (`hilockin-3336`) | Project `paatti` (`paatti.vercel.app`) + sandbox | Its sandbox session expired around 11:15 IST, so that link is dead. Not controllable from the current connector |

**Why the backend can't be a normal Vercel Function:** it keeps live call state in memory, uses long-lived WebSockets (dashboard hub, phone audio, Twilio streams) and writes SQLite. Vercel Functions don't share memory across instances and close sockets at max duration. Vercel Sandbox works but is capped at 45 minutes per session on the Hobby plan.

**Connecting the dashboard to a backend:**
1. Start the backend somewhere public over HTTPS (for example, laptop + `cloudflared`).
2. Start it with `PAATTI_CORS_ORIGIN_REGEX='https://[a-z0-9-]*paatti[a-z0-9-]*\.vercel\.app'` (or add `https://paatti-app.vercel.app` to `PAATTI_CORS_ORIGINS`) and `PAATTI_PUBLIC_URL=<its URL>`.
3. In Vercel → `paatti-app` → Settings → Environment Variables, set `VITE_API_BASE=<backend URL>`, then redeploy.
4. **QR phone calls:** these use the backend's own URL (`public_url`), so the phone page is served by the backend.

**Docker / Render:** the `Dockerfile` (single container: builds the dashboard, serves it with FastAPI) and `render.yaml` blueprint exist. The Docker build couldn't be tested in the build environment (Docker Hub rate limit), so build it once locally first.

---

## 12. Verified live (with real keys, 5 Nov)

- **Gemini:**
  - **Models:** the key works in AI Studio mode. `gemini-2.5-flash` is closed to new users (404). `gemini-3.8-flash` was overloaded (503) at times. `gemini-3.5-flash` and `gemini-3.1-flash-lite` work.
  - **Latency:** about 5 s, even for a one-word reply (that's why timeouts are 7 s/12 s and there's a second filler).
  - **Live replies:** an in-character Gemini reply came back in 2 s through the public URL.
- **Sarvam:**
  - **First key:** invalid (403).
  - **Second key:** works. Text-to-speech gave 8 s of audio in 1.4 s; speech-to-text took 0.37 s.
  - **Voices:** `anushka` is **not** a Bulbul v3 voice (fixed to `kavitha`/`roopa`).
  - **Speech-to-text output:** returns native script by default; `mode=translit` returns romanised text with digits as numerals (now the default).
  - **Push-to-talk:** the dashboard's hold-to-talk used to send WebM labelled as WAV (400). It now converts to WAV in the browser, verified in Chromium.
- **End to end (on a Vercel Sandbox, before it was stopped):**
  - **Browser phone:** the greeting came back as real Sarvam audio.
  - **Voice turn:** "This is CBI. Transfer to account number 5021003456", then Paatti read back "5-0-2-1-0-0-3-4-5-6. Sariya?", with audio.
- **Not verified live:** Twilio (no account), the Docker image build, and `paatti-app.vercel.app` talking to a backend (no backend running).

---

## 13. Quality status

- **Backend:** 142 pytest tests, about 95% coverage. Tests run offline: Gemini, Sarvam and Twilio are faked, and each test gets an isolated DB and cache.
- **Frontend:** 9 vitest tests (reducer and helpers).
- **Evals:** 8 scripts. Offline: 100% key-field accuracy (24/24 account/IFSC/UPI), 88/88 checks, read-back gate pass, persona in character. The `--llm` eval has not been run against live Gemini (to save quota).
- **Browser E2E (Playwright):**
  - **Dashboard demo:** the account card locks on read-back.
  - **QR phone flow:** a second page joins, the dashboard auto-follows, and the dispatch to 1930 shows within the golden hour.
  - **Errors and layout:** no console errors, and no horizontal scroll at phone width.
- **Reviews:** everything-claude-code `security-reviewer` and `code-reviewer` agents ran; their findings were fixed. These included:
  - **Abuse limits:** daily caps, bounded queues, turn cap.
  - **Watch-only phone calls:** dashboards can't drive or end a live phone call.
  - **Masked caller IDs.**
  - **Twilio hardening:** stream handshake timeout and webhook replay protection.
  - **VAD noise lock-up.**
  - **Per-call turn lock.**
  - **Browser downsampler phase.**
  - **Phone redial.**

---

## 14. Known limitations and risks

- **No dashboard login:** reports and dispatch are open to anyone with the link (prototype). Operator token only gates outbound calls and caller-ID unmasking.
- **Mock dispatch:** it contacts no one. The NCRP field layout is a proposed format.
- **Twilio untested live:** trial accounts play a trial message and only call verified numbers; Indian numbers need paperwork.
- **Gemini latency (~5 s):** masked by fillers. The reply cache only covers rehearsed lines.
- **STT digit errors:** speech-to-text can mishear digits (in a test, "HDFC 000 1234" came back with an extra zero). The validators and the read-back gate catch this, which is the point of the design.
- **SQLite:** fine for a single instance; a multi-agency deployment would move to Postgres.
- **Keys in chat history:** the API keys were shared in the AI chat transcript. **Rotate them after the hackathon.**

---

## 15. BeyondHack 2.0 rounds (5 Nov 2026, SRM IST) — see `docs/ROUNDS.md`

- **Round I (~11:00):** live demo and Q&A. Show problem, solution, prototype, stack, progress, team explanation.
- **Round II (eval 14:45):** functional app, UI, backend, **database**, **architecture diagram**, **ER diagram**, **GitHub repo**, **docs**, **deployment link**, technical demo, plus an **updated PPT uploaded to the organisers' Drive** (on hold at the user's request).
- **Round III (from 15:30):** 5-minute presentation and live demo; no development.

**Deliverable status:**
- **Done:** app (7), UI (8), backend (9), SQLite DB (10), architecture (`docs/ARCHITECTURE.md`, 11), ER diagram (`docs/DATABASE.md`, 12), docs (14).
- **Partly done:** repo (13), make sure it's public with default branch `main`; deployment (15), dashboard live but backend host still needed.
- **Pending:** PPT.

**5-minute pitch outline:**

| Time | Section | What to show or say |
|---|---|---|
| 0:00 | Problem | Rs 4,057 cr; the golden hour; the mule account is the choke point |
| 0:40 | Meet Paatti | The persona |
| 1:10 | Live demo | A judge plays the scammer on a phone; the account card locks after read-back |
| 3:10 | Report | Report, then 1930 dispatch with the golden-hour timer ("leads, not verdicts") |
| 3:50 | How it works and why it's safe | Gemini rotation, Sarvam voice, SQLite mule registry, code read-backs, verbatim quotes, injection evals |
| 4:30 | Impact | "Hand it to Paatti", seeded numbers, telco lines, persona pack |

**Judge Q&A essentials:**
- **How does a scammer reach Paatti?** Forwarding or conferencing, seeded numbers, and telco honeypot lines.
- **Framing risk:** leads, not verdicts. Analyst review, corroboration with MuleHunter, never auto-freeze.
- **"You're an AI":** prepared in-character replies plus the output guard, tested by evals.
- **Accuracy:** 100% on scripted calls; the live demo is the unscripted test.
- **Latency:** fillers mask it.
- **Limits:** model rotation, local rate limiting, cache, script fallback.
- **Throwaway accounts:** they're still live mules; repeat sightings raise confidence.
- **Ethics:** our own line only, no real person's data, legal review before any pilot.

---

## 16. History of key decisions

1. **MVP:** FastAPI + React built from the pitch deck with the everything-claude-code TDD/eval workflow. Read-back generated in code; verbatim-quote gate.
2. **Hackathon-winning tweaks:** a "hand it to Paatti" distribution story, a framing-risk safeguard, adversarial evals, the persona pack, proof by evals, and dispatch to 1930.
3. **Phone demo:** Option A (Twilio real calls) and Option B (browser phone via QR), sharing one voice pipeline.
4. **Gemini instead of Claude:** the team uses Gemini. Claude is kept optional. Model and key rotation plus a prepared-replies layer so the demo never hits limits.
5. **SQLite + ER diagram:** added because Round II requires a database.
6. **Live-key testing:** found the invalid Sarvam voice, the native-script STT, Gemini latency and the WebM push-to-talk 400. All fixed.
7. **Vercel:** first a Sandbox (45-minute limit) on an account without GitHub. Then a new account (rayaanarora47@gmail.com), a GitHub-linked dashboard project `paatti-app`, and the sandbox dropped at the user's request. The backend host is still to be chosen.

---

## 17. Next steps (in order)

1. **Pick a backend host:**
   - **Laptop + cloudflared (fastest):** run the backend and the tunnel.
   - **Render:** use the blueprint in `render.yaml`.
2. **Wire up the dashboard:** set `VITE_API_BASE` on Vercel `paatti-app` to the backend URL and redeploy. Check that `/api/health` shows `gemini` and `sarvam`.
3. **Rehearse:** one full QR phone call and the dashboard demo. Keep text mode and a backup video ready.
4. **GitHub:** repo public, default branch `main`, delete old branches if wanted.
5. **PPT:** updated deck for the Round II upload, when the user asks.
6. **After the event:** rotate all keys; optional Twilio setup; operator login on the dashboard.
