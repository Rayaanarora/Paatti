# 👵 Paatti (பாட்டி) — Autonomous AI Honeypot for Digital Arrest Frauds

> **"Daisy wastes scammers' time. Paatti makes them hand over their mule accounts before the next victim pays."**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-paatti.onrender.com-success?style=for-the-badge&logo=render)](https://paatti.onrender.com/)
[![Beyond Hacks 2.0](https://img.shields.io/badge/Beyond%20Hacks%202.0-Top%2010%20Finalist-orange?style=for-the-badge)](https://paatti.onrender.com/)
[![Tests](https://img.shields.io/badge/Tests-223%20passing%20(95%25%20cov)-brightgreen?style=for-the-badge)](https://paatti.onrender.com/)
[![Voice AI](https://img.shields.io/badge/Voice%20AI-Sarvam%20AI%20(Indic)-blue?style=for-the-badge)](https://www.sarvam.ai/)
[![LLM Engine](https://img.shields.io/badge/LLM-Google%20Gemini%202.5%2F3.5-blueviolet?style=for-the-badge)](https://ai.google.dev/)

**Live Deployment:** [https://paatti.onrender.com/](https://paatti.onrender.com/) *(Note: If waking from dormant free tier, please allow ~30s).*

---

## 🏆 Beyond Hacks 2.0 (SRM IST) — Top 10 Finalist
Built by **Team LockIn** (Faheem, Rushi, Ray, Deeps).

Following our Top 10 finish, judges and cyber-fraud mentors recommended institutionalizing the pipeline for government and law-enforcement pilots—specifically integrating with **I4C (Indian Cybercrime Coordination Centre)**, the **NCRP 1930 Helpline**, and **telecom/bank fraud prevention consortiums**.

---

## 🚨 The Problem: The ₹4,057 Crore "Digital Arrest" Epidemic

Between 2022 and 2024, Indian citizens lost over **₹4,057 crore** to "Digital Arrest" scams across nearly 3 lakh complaints:
1. **The Fear Hook:** Fraudsters impersonate CBI, ED, TRAI, or state police over WhatsApp/Skype video or phone calls. They claim the victim's Aadhaar is linked to money laundering or illicit courier shipments.
2. **The Extraction:** Under psychological terror, the victim is coerced into liquidating savings and sending money to "RBI verification accounts."
3. **The Disappearance:** Money jumps through 13+ layers of mule bank accounts in under 30 minutes.
4. **The Response Gap:** If reported within the **2-hour "Golden Hour"**, 70–80% of funds can be frozen and recovered. After that window, recovery drops to 15–20%.

**Existing fraud tools protect one victim at a time after the money is gone. Paatti traps the scammer's operational infrastructure before the next victim transfers a single rupee.**

---

## 💡 How Paatti Works

```
                                      [ THE DIGITAL ARREST TRAP ]
                                      
  Scammer Call (Phone / QR / Twilio)
               │
               ▼
   [ Sarvam Saaras v3 STT ] ────────► [ Verbatim Quote Validator ]
               │                                   │
               ▼                                   ▼
      [ Kamala Paatti AI ] ──────────► [ Read-Back Verification Gate ]
      (Slow, terrified 72yo                     │
       retired school teacher)                  │ "Account 5021 0034 7781 9920...
               │                                │  Sariya kanna?"
               ▼                                ▼
   [ Sarvam Bulbul Indic TTS ]       [ Scammer Voice: "Haan, correct" ]
               │                                   │
               │                                   ▼
               │                        [ LOCKED MULE INTEL ]
               │                        • Beneficiary Bank & IFSC
               │                        • Account / UPI VPA
               │                        • Time-stamped Verbatim Quote
               │                        • Cross-Session Sighting Count
               │                                   │
               ▼                                   ▼
     [ Scammer Wastes 30+ Mins ]       [ Golden-Hour Dispatch ]
     (Complains about slow internet,   • Bank Fraud Desk API Hand-off
      cat spills milk, misplaced specs) • NCRP 1930 / I4C Portal JSON
                                       • Court-Admissible Forensic PDF (BNS 318/319)
```

---

## 🚀 Start Everything with One Click

Needs [Python 3.11+](https://www.python.org/downloads/) and [Node.js LTS](https://nodejs.org/).

- **Windows:** double-click `start.bat`
- **Mac / Linux:** run `./start.sh`

It installs dependencies, starts Paatti, creates a public HTTPS link (so the phone mic works), and opens it in your browser. Open that link on the laptop, click **Phone call via QR**, and scan with the phone. Keep the window open during the demo. Optional keys for AI replies and real voice go in `.env` (created on the first run).

---

## 👵 Personas: 12 Multilingual Grandparents

Twelve grandparents, each with their own language, region, backstory, and voice, so a repeat caller never meets the same person twice. Pick one on the dashboard, or choose **Random** (or set `PAATTI_PERSONA=random`) for a different one every call.

| Key | Persona | Language | From | Voice Profile |
|---|---|---|---|---|
| `paatti` *(default)* | Kamala Paatti | Tamil / Indian English | Mylapore, Chennai | Elderly retired teacher, gentle, hard of hearing |
| `thatha` | Rangasamy Thatha (male) | Kongu Tamil | Coimbatore | Retired agricultural officer |
| `dadi` | Sharda Dadi | Hindi | Lucknow | Anxious grandmother, religious, prays during call |
| `dadaji` | Ramesh Chand Dadaji (male) | Hindi (Marwari touch) | Jaipur | Retired shopkeeper, checks passbook repeatedly |
| `ammamma` | Lakshmi Ammamma | Telugu | Vijayawada | Devout, offers to do puja for caller |
| `ajji` | Shantamma Ajji | Kannada | Mysuru | Speaks soft Kannada, checks door latch |
| `ammachi` | Mariamma Ammachi | Malayalam | Kottayam | Syro-Malabar grandmother, easily flustered |
| `thakuma` | Bani Thakuma | Bengali | Kolkata | Retired headmistress, reads slowly |
| `aaji` | Sulabha Aaji | Marathi | Pune | Retired bank clerk, insists on repeating digits |
| `ba` | Hansaben Ba | Gujarati | Ahmedabad | Vegetarian grandmother, tea break excuses |
| `bauji` | Harbhajan Singh Bauji (male) | Punjabi | Ludhiana | Ex-serviceman, hard of hearing, very polite |
| `uncle` | Joseph Uncle (male) | Goan English | Margao, Goa | Calm, slow-paced church deacon |

`python evals/run_evals.py --persona all` runs every scam script against every persona.

---

## ✨ Features

| Capability | What It Does |
|---|---|
| 📞 **Real Phone Calls** | Twilio programmable voice: dial in directly, or the dashboard rings an allowlisted phone with natural Indic voice. |
| 📱 **Browser Phone (QR Code)** | The evaluator scans a QR code on their phone; their mobile becomes the scammer's handset with realistic call UI. |
| 🧓 **12 Multilingual Personas** | Injection-hardened persona matrix covering 8 Indian languages and dialects with layered output guards. |
| 🔁 **Read-Back Verification Gate** | Paatti reads extracted numbers back one digit at a time. Identifiers lock **only after the scammer verbally confirms**. |
| 🔎 **Dual Extractors + Zero Hallucination** | Instant regex rules + Gemini JSON extraction. Enforces verbatim quote validation to prevent false positives. |
| 🖥️ **Live Forensic Dashboard** | Real-time transcript, 4-stage psychological tracker, locked intel cards, time-wasted timer, and active call monitor. |
| 📄 **Mule Intel Report (PDF & JSON)** | Generates actionable intelligence packets with SHA-256 audio hashes, BNS 318/319 evidentiary grounding, and NCRP-ready formats. |
| 🚨 **Golden Hour Dispatch Engine** | Automated mock hand-off to I4C / NCRP 1930 / Bank Fraud Desks within the critical 2-hour recovery window. |
| 🗄️ **Persistent SQLite Database** | Full session state, turn history, and a cross-session mule registry detecting syndicated repeat accounts. |
| ⚡ **Resilient Zero-Downtime Pipeline** | Gemini model/key rotation, local rate limiting, pre-computed in-character reply cache, and offline scripted fallback. |

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Frontend** | React 18, Vite, WebSockets, Web Audio API | Live real-time dashboard and mobile call simulator |
| **Backend** | Python 3.11+, FastAPI, Uvicorn, Pydantic | Asynchronous state machine and forensic engine |
| **LLM Engine** | Google Gemini 2.5 Flash / 3.5 Flash Lite | Persona roleplay & JSON intelligence extraction |
| **Voice AI** | Sarvam AI (Saaras STT + Bulbul Indic TTS) | Low-latency natural Indic conversational voice |
| **Telephony** | Twilio Programmable Voice | Bidirectional audio streaming for real telecom lines |
| **Database** | SQLite3 (`paatti.db`) | Local persistence & cross-session repeat mule registry |
| **Reporting** | ReportLab PDF Engine | Forensic proof sheets & NCRP 1930-compliant JSON |
| **Deployment** | Render, Docker, Cloudflare Tunnels | Live cloud hosting & public HTTPS tunneling |

---

## 💻 Manual Setup & Local Execution

### 1. Clone Repository
```bash
git clone https://github.com/Rayaanarora/Paatti.git
cd Paatti
```

### 2. Frontend Setup
```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. Backend Environment
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

### 4. Configure Environment (`backend/.env`)
```env
# Optional: Runs completely offline with cached assets if no keys provided
GEMINI_API_KEY=your-gemini-api-key
SARVAM_API_KEY=your-sarvam-api-key
PAATTI_PERSONA=paatti
```

### 5. Launch Server
```bash
uvicorn paatti.main:app --host 0.0.0.0 --port 8000
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

| Keys Set | Persona Mode | Extractor | Voice Engine |
|---|---|---|---|
| *None* | Scripted + Prepared Cache | Regex Rules | Browser Speech Synthesis / Pre-cached WAV |
| `GEMINI_API_KEY` | Gemini 2.5 / 3.5 Chain | Regex + LLM Extraction | Browser Speech Synthesis / Pre-cached WAV |
| `SARVAM_API_KEY` | Gemini 2.5 / 3.5 Chain | Regex + LLM Extraction | Sarvam Bulbul Indic TTS (Natural Indian Voice) |
| Twilio Credentials | Gemini 2.5 / 3.5 Chain | Regex + LLM Extraction | Twilio Telecom Audio Streaming |

---

## 🧪 Tests & Quality Assurance

Paatti includes 223 rigorous tests covering state machine transitions, regex extractors, read-back verification, and red-team attacks:
```bash
# Run backend test suite (223 tests with 95% code coverage)
cd backend && pytest --cov=paatti tests/

# Run frontend tests
cd frontend && npm test

# Run adversarial benchmark suite
python evals/run_evals.py

# Run adversarial red-team prompt injection suite (62 attack vectors across 12 personas)
python evals/redteam.py
```

### Benchmark Results
- **Checks:** 88 / 88 passed.
- **Account / IFSC / UPI Capture Rate:** 100% (24 / 24 critical fields).
- **Adversarial Vectors Passed:** Prompt injection, out-of-band OTP demands, identity challenges (*"Are you an AI?"*).
- **Read-Back Gate:** 0 unconfirmed false positives leaked to police/bank report fields.

---

## 🔒 Safety, Legality & Ethics

- **Leads, Not Verdicts:** Paatti produces prioritized forensic intelligence leads for trained human analysts at bank fraud desks. It never triggers automated account freezing without human review.
- **Verbatim Evidence Anchoring:** Every extracted field must correlate to an exact timestamped transcript quote.
- **Chain of Custody:** Audio recordings are hashed with SHA-256 for evidentiary compliance under Sections 318/319 of the Bharatiya Nyaya Sanhita (BNS).
- **Can't Be Talked Out of Character:**
  1. *Nothing to leak:* Persona instructions are written entirely in the grandmother's world (no mention of AI, honeypots, police, or extraction).
  2. *Code-level defense:* "Are you an AI?", prompt injection, and OTP requests are intercepted in Python before ever reaching the LLM.
  3. *Output guards:* Model output is inspected before synthesis; any tech terms or hallucinated digits are replaced with prepared confused grandmother dialogue.
- **Phone Line Security:** Signed Twilio webhooks, single-use stream tokens, replay protection, and masked phone numbers in dashboard listings.

---

## 📚 Documentation Directory

- **[docs/CONTEXT.md](docs/CONTEXT.md)** — Complete project context, APIs, configuration, deployment state, and roadmap.
- **[docs/STUDY.md](docs/STUDY.md)** — In-depth walkthrough of the digital arrest crisis, honeypot mechanics, and safety guidelines.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — System architecture, real-time audio pipeline, and WebSocket protocols.
- **[docs/DATABASE.md](docs/DATABASE.md)** — SQLite schema, ER diagram, and cross-call mule detection queries.
- **[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)** — Rehearsed 8-turn confrontation script with target timestamps and expected triggers.
- **[docs/DEPLOY.md](docs/DEPLOY.md)** — Step-by-step cloud deployment (Render, Docker, Cloudflare, Twilio).
- **[docs/ROUNDS.md](docs/ROUNDS.md)** — Hackathon evaluation deliverables, 5-minute pitch script, and judge Q&A breakdown.

---

## 👥 Team LockIn
- **Faheem** — Lead Architecture, Voice Pipeline & Pitch
- **Rushi** — Live Voice Pipeline & Telephony
- **Ray** — Intelligence Extraction & Model Resiliency
- **Deeps** — Forensic Evidence, Testing & Compliance

*Built with passion at Beyond Hacks 2.0 (SRM IST).*
