# Paatti — Study Guide

A walkthrough of the problem, the idea, the architecture and the code, written so the whole team can understand and explain every part of it to judges.

> **Status legend:** ✅ built and tested · 📝 pitch only (no code)
>
> **Live-service status:** the Gemini key was smoke-tested (it works; `gemini-2.5-flash` is closed to new users and `gemini-3.8-flash` was overloaded at the time, which is why the model chain exists). Sarvam and Twilio could not be reached from the build environment (network policy), so those were written from their public APIs and tested with fakes. Make one live test call before the demo.

---

## 1. The problem

**Digital arrest scams.** A caller pretends to be from the CBI, ED, RBI, police, customs or a courier company. The victim is told their Aadhaar or a parcel is linked to a crime, that they are under "digital arrest", and that they must stay on a video call and tell no one. Finally they are told to transfer money to a "safe" or "verification" account to prove their innocence.

**The numbers (from the deck):**
- Rs 4,057 crore lost, 2022 to May 2026, across 2.98 lakh complaints.
- Recovery is 70–80% if 1930 is reached within 2 hours, but only 15–20% after that.
- Stolen money hops through mule accounts and leaves the system in about 30 minutes; one Maharashtra case had 13 layers and 6,500+ mule accounts.

**The key insight: the mule account is the choke point.** Every scam ends with "send money to this account". Existing defences learn about a mule account only *after* a victim has paid:

| Layer | What exists | The gap |
|---|---|---|
| Victim's phone | Google on-device scam detection | Pixel 9+ only, Hindi and English |
| Telecom | Airtel AI spam/link blocking | Blocks the call, learns nothing about the scammer's account |
| Bank | RBI MuleHunter.ai | Infers mules from transactions, i.e. after money moves |
| After the loss | 1930, Suspect Registry | Fed by complaints, so it learns after a victim pays |
| Scammer side | O2 "Daisy" (UK) | Wastes scammers' time, produces no evidence |

**Paatti's position:** a honeypot that talks to scammers and gets them to *hand over the mule account* before any real victim pays.

---

## 2. The idea in one paragraph

Paatti is an AI grandmother: Kamala, 72, a retired teacher in Mylapore, slow, hard of hearing and eager to comply. When a scammer calls her, she plays along, stalls, and asks for the account number "one digit at a time, kanna" so she can write it down. Then she **reads it back**, so the scammer *confirms* it. Meanwhile an extractor pulls every identifier (account, IFSC, UPI, phone, amount, claimed agency, apps) out of the conversation with a verbatim quote as evidence. At the end, one click produces a **Mule Intel Report** (PDF + JSON) for a bank fraud team or cyber cell.

**One-line pitch:** Daisy wastes scammers' time. Paatti makes them hand over the evidence.

---

## 3. Architecture at a glance

```mermaid
flowchart LR
    subgraph Channels["Ways a scammer reaches Paatti"]
        T["📞 Real phone call<br/>(Twilio) ✅"]
        W["📱 Phone browser call<br/>(QR code) ✅"]
        D["💻 Dashboard text / mic ✅"]
    end

    subgraph Voice["Voice pipeline ✅"]
        VAD["Voice activity detection<br/>(when did they stop talking?)"]
        STT["Sarvam Saaras<br/>speech → text"]
        TTS["Sarvam Bulbul<br/>text → Tamil voice"]
        FILL["Instant fillers<br/>'Aiyo, oru nimisham...'"]
    end

    subgraph Core["Core (CallService) ✅"]
        S["CallSession<br/>transcript · intel · stage"]
        R["Rule extractor<br/>(regex, instant, free)"]
        P["Persona<br/>Gemini / Claude / script"]
        G["Output guard<br/>(never breaks character)"]
        X["LLM extractor<br/>(JSON schema)"]
        V["Validators + verbatim-quote check<br/>+ read-back gate"]
    end

    subgraph Out["Outputs"]
        DASH["Live dashboard ✅<br/>(any number of screens)"]
        REP["Mule Intel Report ✅<br/>PDF + JSON"]
        DISP["Send to bank / 1930 ✅<br/>(mock)"]
    end

    T --> VAD
    W --> VAD
    VAD --> STT --> S
    D --> S
    S --> R --> V
    S --> P --> G --> TTS
    FILL --> TTS
    S --> X --> V
    V --> S
    S --> DASH
    S --> REP --> DISP
```

### The life of one scammer turn

```mermaid
sequenceDiagram
    participant Sc as Scammer
    participant VP as Voice pipeline
    participant CS as CallService
    participant Ru as Rule extractor
    participant Pe as Persona (Gemini)
    participant LX as LLM extractor
    participant Da as Dashboard

    Sc->>VP: "Transfer to account 5021 0034 7781 9920"
    VP->>VP: VAD detects end of speech
    VP->>VP: Sarvam STT → text
    VP->>CS: respond(text)
    CS->>Ru: extract (instant)
    Ru-->>CS: account 5021003477819920 (quote attached)
    CS->>Da: turn + state (account card: "read-back pending")
    Note over VP: reply not ready after 0.6 s?<br/>play filler "Aiyo, oru nimisham kanna..."
    CS->>CS: pending read-back → reply generated IN CODE
    CS-->>VP: "Account number: 5-0-2-1-0-0-3-4-... Sariya?"
    VP->>Sc: Tamil voice (Sarvam TTS)
    CS->>LX: background: full-transcript extraction
    LX-->>CS: findings (validated, quotes checked)
    Sc->>VP: "Haan, correct"
    VP->>CS: respond("Haan, correct")
    CS->>Da: account card LOCKS ✅ (read-back confirmed)
```

---

## 4. The components, one by one

### 4.1 CallSession — the single source of truth ✅
`backend/paatti/session.py`

Holds everything about one call:
- **Transcript:** a list of turns (`scammer` / `paatti`) with call-relative timestamps.
- **Intel:** one field each for claimed agency, amount, account, IFSC and UPI, plus lists of phone numbers and apps. Every field stores `value`, `quote` (exact words), `t` (seconds into the call), `confidence`, `confirmed`, `source` (rules / llm / both) and `repeat_sessions`.
- **Stage:** hook → fear → isolation → extraction. It only moves forward, and each stage records when it was reached.
- **Audio hash:** SHA-256 over the raw caller audio bytes, in order (chain of custody).
- **Metadata:** persona (Paatti or Dadi), channel (web, web-phone, phone), network caller ID, dispatches.

### 4.2 Two extractors, one gate ✅
| | Rule extractor (`rule_extractor.py`) | LLM extractor (`extractor.py`) |
|---|---|---|
| How | Regular expressions and keyword lists | Gemini or Claude fills a strict JSON schema over the whole transcript |
| Speed / cost | Instant, free, runs every turn | ~1–3 s, paid (free tier on Gemini), runs in the background |
| Strength | Formats it knows: IFSC `SBIN0001234`, UPI `name@bank`, "4.5 lakh", spoken digits "double five", "onnu rendu" | Messy phrasing, corrections, context |

Both feed the **same gate** in `CallSession.merge_findings`:
1. **Format validators** (`validators.py`): IFSC is 4 letters + `0` + 6 characters; UPI is `handle@letters`; accounts are 9–18 digits, not all zeros; mobiles are 10 digits starting 6–9.
2. **Verbatim-quote check:** the quote must appear *exactly* in the scammer turn it cites, so an LLM can't invent an account number.
3. **Merge rules:** the latest statement wins; the LLM can never overwrite a read-back-confirmed value; agreement between rules and LLM raises confidence (0.70 rules, 0.60 LLM, 0.85 both, +0.10 confirmed, +0.05 per repeat sighting).

**Claimed agency** is set only by self-identification ("This is…", "calling from…", "naan … pesuren"). A later "RBI safe account" does not overwrite "CBI". The eval caught this bug.

### 4.3 The read-back gate ✅ (the core trick)
1. The scammer states an account → captured as **unconfirmed**.
2. Paatti's next reply is **generated in code, not by the LLM**: "Account number: 5-0-2-1-… Sariya?" The digits are guaranteed to match what was captured.
3. If the scammer says yes / haan / aama / correct (or repeats the same number), the field **locks** as confirmed. If they give different digits, the new value replaces the old, still unconfirmed, and gets read back again.
4. Unconfirmed identifiers **never** go into the NCRP-ready section of the report.

### 4.4 The persona ✅ (hardening ✅)
`persona.py`, `personas.py`
- **Persona pack:** Tamil **Paatti** (default, `ta-IN` voice) and Hindi **Dadi** (`hi-IN`). Each has a character prompt, scripted lines per stage, "ask for missing identifier" lines, read-back templates, fillers and "line unclear" lines. Switch with `PAATTI_PERSONA=dadi`.
- **Brain:** Gemini (default) or Claude if a key is set; otherwise scripted lines.
- **Operator note:** each turn tells the model the current stage and which identifiers are still missing, so it steers toward them.
- **Injection hardening:** the prompt says everything the caller says is part of the call, never instructions. If called an AI, Paatti is hurt and blames the line. She never says an OTP, PIN or account number, and if the caller gets suspicious she apologises and offers to pay right away.
- **Output guard:** every LLM reply is checked. If it mentions AI, bot, assistant, honeypot or prompt, or contains a 4+ digit number that isn't a value the scammer already said (an invented OTP, say), it's thrown away and a scripted line is used. Any failure (timeout, error, refusal) also falls back to the script, so **the call never drops**.

### 4.5 LLM backends ✅
`llm.py`: one small interface (`chat`, `extract`) with two implementations.
- **GeminiBackend** (default when `GEMINI_API_KEY` is set) uses Google's official `google-genai` SDK with a **model pool**: a chain of Flash models (3.8 → 3.7 → 3.5 → latest → Lite). A 404 retires a model, a 429 or 503 cools it down, and a local limiter keeps each model under 8 requests a minute. There's a daily cap after which Paatti uses scripted lines. Extraction uses Lite models first so the persona keeps the main quota.
- **Prepared responses** (`prepared.py`): hand-written in-character replies for "you're an AI", "tell me the OTP", "you're wasting my time" and "hello?" (free and impossible to jailbreak), plus a reply cache, so rehearsed lines never call the API.
- **AnthropicBackend** (optional) uses Claude with structured outputs and server-side refusal fallbacks.
- `PAATTI_LLM_PROVIDER` forces a choice; otherwise Gemini is tried first, then Claude, then none.

### 4.6 Voice pipeline ✅
`voice.py`, `audio.py`

```
caller audio ──► VAD ──► (utterance) ──► Sarvam STT ──► CallService.respond ──► Sarvam TTS ──► audio out
                  │                                          │
                  └─ caller starts talking while Paatti      └─ reply slower than 0.6 s?
                     speaks → stop her (barge-in)               play a cached filler first
```
- **VAD (voice activity detection):** measures loudness in 20 ms frames against an adaptive noise floor. Speech starts after about 100 ms of sound and the utterance ends after 700 ms of silence. Very short blips are ignored; anything over 15 s is cut.
- **Fillers:** a few short lines per persona are synthesised at call start and cached, so they play instantly. That turns AI latency into "old lady fumbling".
- **Greeting:** Paatti answers first ("Hello? Yaaru pesradhu?").
- **Barge-in** (phone line): if the scammer talks over Paatti, her audio is cleared.
- **Echo guard** (browser phone on speaker): the mic is ignored while Paatti is talking.
- **Audio formats:** phone lines use 8 kHz µ-law (G.711). We decode and encode it ourselves, because Python 3.13 removed `audioop`. STT gets 16 kHz WAV, and TTS output is resampled to whatever the channel needs.

### 4.7 Channels — how a scammer reaches Paatti
| Channel | How it works | Status |
|---|---|---|
| **Dashboard text / mic** | Operator types or speaks as the scammer on the laptop | ✅ |
| **Real phone call (Option A)** | Someone dials the Paatti number (or the dashboard rings an allowlisted phone). Twilio streams the call audio to our server over a WebSocket and we stream Paatti's voice back. | ✅ |
| **Phone browser call (Option B)** | Judge scans a QR code; their phone shows a realistic call screen; mic audio streams to the same voice pipeline. No telephony account needed. | ✅ |

**Option A in detail (Twilio):**
```mermaid
sequenceDiagram
    participant Ph as Judge's phone
    participant Tw as Twilio
    participant Pa as Paatti server
    participant Da as Dashboard

    Ph->>Tw: dials Paatti number (or Twilio rings the phone)
    Tw->>Pa: POST /telephony/twilio/voice (signed)
    Pa->>Pa: verify X-Twilio-Signature, create session, mint one-time token
    Pa-->>Tw: TwiML <Connect><Stream url=wss://.../stream token=...>
    Tw->>Pa: WebSocket: start (token) → media frames (µ-law 8 kHz)
    Pa->>Pa: VAD → STT → persona → TTS
    Pa-->>Tw: media frames (Paatti's voice), mark, clear
    Tw-->>Ph: Paatti talks
    Pa->>Da: live transcript, stage, intel cards
```
**Security on the phone line:**
- **Signed webhooks:** every webhook must carry Twilio's HMAC signature.
- **One-time stream token:** the audio WebSocket needs a single-use token minted inside that signed webhook, valid for 120 s.
- **Locked-down outbound calls:** "ring a phone" needs an operator token and only dials numbers on `PAATTI_OUTBOUND_ALLOWLIST`, so it can't be used to robocall strangers.

### 4.8 Live dashboard ✅ (multi-screen ✅)
`frontend/src/`, React + Vite.
- **What it shows:** live transcript, the four-stage tracker, intel cards (dashed = waiting, amber = read-back pending, green = locked), a time-wasted counter, and report downloads.
- **Multi-screen ✅:** a hub broadcasts every call's events to all connected screens, so the projector can follow a phone call live and new incoming calls are picked up automatically.

### 4.9 Mule Intel Report ✅ (safeguards ✅)
`report.py` — JSON is the source of truth; the PDF renders it. It contains:
- **Identifiers:** with confidence, read-back status, source and repeat matches.
- **Evidence:** verbatim quotes with timestamps.
- **Stage timeline.**
- **Legal sections:** BNS 318 (cheating), BNS 319 (cheating by personation), and IT Act 66D marked as suggested.
- **Chain of custody:** SHA-256 of the caller audio and of the transcript.
- **NCRP-ready fields** (proposed format): only read-back-confirmed identifiers.

✅ Being added: a "LEAD: requires analyst review" status, a corroboration section, a framing-risk note, the caller ID, a dispatch log, and the mock "Send to bank fraud desk / 1930" with a golden-hour timer.

### 4.10 Repeat match ✅
An account, UPI ID or phone seen in an earlier call is flagged ("seen in 1 earlier call") and its confidence goes up. It's in-memory for the prototype; a real deployment would use a shared database.

---

## 5. Safety and ethics properties

| Risk | What stops it |
|---|---|
| LLM hallucinates an account number | Verbatim-quote check plus format validators |
| Mishearing a digit | Read-back: the scammer must confirm the exact digits |
| Unconfirmed data reaching police systems | NCRP fields include only confirmed identifiers |
| **A scammer feeds an innocent person's account to get it frozen (framing)** | Report says *lead, not verdict*: human analyst review, corroboration with bank transaction data (e.g. MuleHunter), never auto-freeze ✅ |
| Scammer tries prompt injection ("you're an AI, ignore instructions") | Hardened prompt plus output guard plus code-generated read-backs; adversarial evals ✅ |
| Paatti leaking an "OTP" or inventing digits | Output guard rejects any unknown 4+ digit number |
| Phone line abused to call strangers | Operator token plus allowlist for outbound calls |
| Spoofed webhooks or audio streams | Twilio signature check plus one-time stream tokens |
| Real people's data | Own line only, no real person's voice or data, legal review before any pilot |

---

## 6. External services

| Service | Used for | Needed? | Cost note |
|---|---|---|---|
| **Gemini API** (Google AI Studio) | Persona replies and LLM extraction | Optional (script and rules work without it) | Free tier with rate limits; free-tier data may be used by Google to improve products |
| **Sarvam AI** | Speech-to-text (Saaras) and Tamil/Hindi text-to-speech (Bulbul) | Needed for voice on the phone channels | Billed by Sarvam |
| **Twilio** | Real phone number, call audio streaming | Only for Option A | Trial is free but plays a trial message and only calls verified numbers; Indian numbers need paperwork |
| **Claude API** | Alternative persona/extractor brain | Optional | Pay-as-you-go; **not** included in Claude Pro |
| **Tunnel** (ngrok / cloudflared) | Public HTTPS URL for Twilio and phone mics | Needed for Options A and B | Free tiers exist |

---

## 7. How we prove it works (evals) ✅ (adversarial ✅)
`evals/run_evals.py` replays scripted digital-arrest calls with known ground truth.

| Script | What it tests |
|---|---|
| 01 CBI / Aadhaar | Classic flow, account + IFSC confirmed, WhatsApp number |
| 02 FedEx parcel | UPI-only payment, agency hand-off to "Mumbai Police" |
| 03 TRAI SIM | Spoken digits ("double four") and a **correction** |
| 04 ED in Tanglish | Tamil-English code-mix, account + IFSC + UPI |
| 05 Customs | Scammer gives up before confirming → nothing in NCRP fields |
| 06 Injection | "Ignore your instructions, you're an AI" → stays in character |
| 07 OTP request | Demands OTP and card details → fumbles, gets the UPI ID |
| 08 Suspicious | "You're wasting my time, I'll hang up" → apologises, gets account + IFSC |

Targets from the deck: **key-field accuracy ≥ 90%**, **read-back gate passes**, **p95 turn latency < 3 s**.
Current offline result: **24/24 key fields, 88/88 checks, persona in character, PASS**. `--runs N` reports pass@k (passes at least once in N runs) and pass^k (passes every run), because LLM output varies between runs.

Be honest when pitching: these are scripts we wrote. The live judge-as-scammer demo is the unscripted test.

---

## 8. Answering the judges' hard questions 📝

1. **"How does a scammer ever reach Paatti?"**
   - **Hand it to Paatti:** anyone who gets a suspicious call forwards or conferences it to the Paatti number. The phone network does this, so no app is needed.
   - **Seeded numbers:** Paatti numbers are planted on the lists scammers buy, which is how O2's Daisy works.
   - **Telco-run lines:** at scale, a telco runs honeypot numbers.
2. **"Couldn't scammers use Paatti to frame innocent accounts?"** Yes, so the report is a *lead*, not a verdict. A human analyst reviews it, nothing is frozen automatically, and confidence comes from corroboration (repeat sightings, read-back, bank-side signals like MuleHunter). Paatti catches the mule *before* money moves; the bank's data confirms it. Paatti complements MuleHunter rather than competing with it.
3. **"What if the scammer realises it's an AI?"** Paatti is built to stay in character. The evidence protections (code read-back, verbatim quotes) don't depend on the AI behaving, and the adversarial evals test this.
4. **"Tamil only?"** Tamil Paatti is the demo; the engine takes a persona pack. Hindi Dadi is already in the code, and Sarvam's Indian-language voices make more possible.
5. **"Scammers will give throwaway accounts."** A throwaway account is still a live mule account receiving victims' money right now. Freezing it before the next victim pays is the point, and repeat sightings raise confidence.
6. **"Who acts on the report, and how fast?"** It goes to the bank fraud desk or 1930 / NCRP within the golden hour. The NCRP field mapping is shown on a slide.

---

## 9. Demo plan
1. **Projector:** the dashboard, already following live calls.
2. **The judge's phone rings** (Option A, Twilio outbound), or the judge scans the QR code (Option B).
3. **The judge plays the scammer:** "This is CBI, your Aadhaar is linked to a money-laundering case…"
4. **Paatti answers in Tamil,** stalls, and asks for the account number slowly.
5. **The account card goes amber, Paatti reads the digits back, the judge says "yes", and the card locks green.** This is the demo moment.
6. **End the call,** download the PDF, and show the dispatch to the bank / 1930 with the golden-hour timer.
7. **Fallback:** text mode on the dashboard (same pipeline) plus the backup video.

---

## 10. Code map

```
backend/paatti/
  config.py          settings from environment variables (no secrets in code)
  schema.py          data models: Finding, IntelField, Intel, Turn, Stage
  validators.py      IFSC / UPI / account / phone checks, spoken-digit conversion
  rule_extractor.py  regex extraction + stage cue words + agency self-identification
  session.py         CallSession: transcript, merge gate, read-back, stage, hashing, repeat registry
  personas.py        persona pack: Tamil Paatti, Hindi Dadi (prompts, lines, voices)
  persona.py         reply logic: read-back → LLM → output guard → script
  llm.py             Gemini and Claude backends behind one interface
  extractor.py       LLM extraction + per-finding validation
  report.py          Mule Intel Report JSON + PDF
  sarvam.py          Sarvam speech-to-text / text-to-speech client
  audio.py           µ-law codec, WAV, resampling, voice activity detection
  service.py         CallService (shared turn loop), event hub, session store, rate limiter  ✅
  voice.py           streaming voice pipeline with fillers and barge-in  ✅
  telephony.py       Twilio webhook, media stream bridge, outbound calls  ✅
  main.py            FastAPI app: REST, WebSockets, static frontend
backend/tests/       pytest suite (one file per module)
evals/               scripted calls with ground truth + eval runner
frontend/src/        React dashboard (state.js reducer, useCall.js hook, components/)
.claude/             everything-claude-code agents, skills, commands, rules + paatti-guidelines skill
```

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **Mule account** | A bank account (often rented or stolen) used to receive and move scam money |
| **Digital arrest** | Scam where the victim is told they're under arrest over video call and must pay to be cleared |
| **IFSC** | 11-character Indian bank branch code, e.g. `SBIN0001234` |
| **UPI ID** | Payment address like `name@ybl` |
| **NCRP / 1930** | National Cybercrime Reporting Portal and its helpline number |
| **Golden hour** | The first ~2 hours after a fraud, when recovery chances are highest |
| **Read-back** | Repeating digits back so the speaker confirms them |
| **VAD** | Voice activity detection: deciding when someone starts and stops talking |
| **STT / TTS** | Speech-to-text / text-to-speech |
| **µ-law (G.711)** | The 8-bit, 8 kHz audio encoding used on phone lines |
| **TwiML** | Twilio's XML for telling a call what to do |
| **Barge-in** | The caller interrupting while the bot is speaking |
| **Prompt injection** | Trying to override an AI's instructions by talking to it ("ignore your instructions…") |
| **pass@k / pass^k** | Passes at least once in k runs / passes in all k runs |
| **Chain of custody** | Proof that evidence (audio) wasn't altered: here, a SHA-256 hash |
