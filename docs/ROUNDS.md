# BeyondHack 2.0 — Round-by-round playbook

Mapped to the official deliverables (numbers in brackets). Tick each box before you're called.

---

## Round I — Idea & initial prototype (evaluation ~11:00)

Live demo and judge Q&A; no PPT submission.

| Deliverable | What to show | Where |
|---|---|---|
| [1] Problem statement | Rs 4,057 cr lost; recovery 70–80% within 2 h vs 15–20% later; the mule account is the choke point | `docs/STUDY.md` §1 |
| [2] Proposed solution | Paatti talks to scammers and makes them *confirm* their mule account via read-back | `docs/STUDY.md` §2 |
| [3] Initial prototype | Dashboard: run the demo script lines and watch the account card lock | http://localhost:8000 |
| [4] Technology stack | React + FastAPI + SQLite; Gemini (persona + extraction); Sarvam (Tamil speech); Twilio (phone line) | slide / README |
| [5] Development progress | 136 backend tests, 8 eval scripts, 50/50 → 88/88 checks; git history | `git log`, `evals/` |
| [6] Team explanation | Each member explains their part (see "Who explains what" below) | |

**90-second Round I demo:**
1. "Every digital-arrest scam ends with *send money to this account*. Today banks learn about that account only after someone has paid."
2. Click **Start call**, play the demo lines: CBI → arrest → isolation → account number.
3. Point at the amber account card → Paatti reads the digits back → "Yes correct" → **green: locked**.
4. Click **Download PDF**: evidence quotes, BNS 318/319, SHA-256 hash, NCRP-ready fields.
5. "Next: real phone calls, which already work, and the report reaching 1930 inside the golden hour."

---

## Round II — Final development (evaluation 14:45) ✅ checklist

| # | Deliverable | Status / where |
|---|---|---|
| 7 | Functional application | ✅ dashboard, browser phone (QR), Twilio phone line, report, dispatch |
| 8 | Frontend / UI | ✅ React dashboard + mobile call screen (`frontend/src`) |
| 9 | Backend | ✅ FastAPI REST + WebSockets, voice pipeline, Gemini pool, Sarvam, Twilio (`backend/paatti`) |
| 10 | Database | ✅ SQLite: 7 tables incl. cross-call mule registry (`backend/paatti/db.py`) |
| 11 | System architecture | ✅ `docs/ARCHITECTURE.md` |
| 12 | ER diagram | ✅ `docs/DATABASE.md` |
| 13 | GitHub repository | ⬜ make the repo **public** and merge the branch to `main` |
| 14 | Documentation | ✅ `README.md`, `docs/` |
| 15 | Deployment | ⬜ Render blueprint (`render.yaml`) or laptop + tunnel; **open the link and check it works** |
| 16 | Technical demonstration | ✅ see the Round III script |
| PPT | Updated deck | ⬜ upload `Paatti_BeyondHack2_Round2.pptx` to the organisers' Drive link |

**Before 14:45:**
- [ ] Deployment link opens and `/api/health` says ok
- [ ] Repo public; README renders on GitHub (the mermaid diagrams render there)
- [ ] PPT uploaded and opened from the Drive link to check it
- [ ] One full rehearsal of the phone demo

---

## Round III — Final presentation (5 minutes, from 15:30)

No development time. Everything must already work.

### 5-minute script

| Time | Who | What |
|---|---|---|
| 0:00–0:40 | Speaker 1 | **Problem.** "Rs 4,057 crore lost to digital arrest scams. If 1930 hears within two hours, 70–80% is recovered; after that, 15–20%. Every scam ends with one thing: a mule account. And every defence today learns that account *after* a victim pays." |
| 0:40–1:10 | Speaker 1 | **Idea.** "Meet Kamala Paatti, 72, retired teacher in Mylapore. She's slow, hard of hearing and very eager to pay. She's also an AI honeypot." |
| 1:10–3:10 | Speaker 2 + a judge | **Live demo.** A judge plays the scammer on a phone (Twilio ring or QR). Projector shows the dashboard following live. Paatti asks for the account digit by digit, reads it back, the judge confirms → card locks green. Point at: stage tracker, time-wasted counter, evidence quotes. |
| 3:10–3:50 | Speaker 3 | **Output.** End call → PDF report → **Report to 1930** → golden-hour timer and reference. "Leads, not verdicts: an analyst confirms with bank data before any freeze." |
| 3:50–4:30 | Speaker 4 | **How it works and why it's safe.** Gemini with model rotation, Sarvam Tamil voice, SQLite mule registry. Read-backs generated in code, verbatim-quote check, never breaks character (tested with injection evals). |
| 4:30–5:00 | Speaker 1 | **Impact and next.** "Hand it to Paatti: forward a suspicious call to her number. Seeded numbers on scammer lists, telco honeypot lines, Hindi Dadi already built. Every call wastes a scammer's time and takes away their collection account." |

**If the live voice fails:** say "same pipeline, text mode", then use the dashboard demo lines (30 s), then the backup video.

### Who explains what (everyone must know the whole project)
| Member | Owns | Must be able to answer |
|---|---|---|
| 1 | Voice + phone (Sarvam, Twilio, VAD, fillers) | "What's the latency?" "What if they interrupt?" |
| 2 | Extraction + evals (rules, Gemini, validators, read-back) | "How do you know the account is right?" "Accuracy?" |
| 3 | Dashboard + report + database | "Show the ER diagram." "What's in the report?" |
| 4 | Persona, safety, pitch | "What if the scammer realises?" "Ethics?" |

### Judge Q&A cheat sheet
| Question | Answer |
|---|---|
| How does a scammer reach Paatti? | Anyone who gets a suspicious call forwards or conferences it to Paatti's number; Paatti numbers seeded onto scammer lists (O2 Daisy model); telco-run honeypot lines at scale. |
| Could scammers frame an innocent account? | Yes, so the report is a *lead*, never an automatic freeze. Confidence comes from read-back, rules + AI agreement and repeat sightings across calls; banks corroborate with transaction signals like MuleHunter. Paatti complements MuleHunter: we catch the mule before money moves. |
| What if the scammer says "you're an AI"? | Hand-written in-character replies for those moments (no AI involved), plus an output guard that blocks any AI admission or invented digits. Tested by the injection, OTP and suspicious-caller evals. |
| Accuracy? | 100% on account / IFSC / UPI across 8 scripted calls including adversarial ones; read-back gate passes. These are our own scripts; the live demo is the unscripted test. |
| Latency? | Rule extraction is instant; a cached filler ("Aiyo, oru nimisham kanna…") plays within 0.6 s while the AI replies, so pauses sound like an old lady fumbling. |
| What if Gemini hits its limit? | Model rotation across several Gemini models (and keys), a local rate limiter under free-tier limits, cached replies, and a scripted fallback. The call never drops. |
| Throwaway accounts? | A throwaway account is still a live mule account taking victims' money right now; freezing it early is the point. Repeat sightings raise confidence. |
| Only Tamil? | Persona pack: Hindi "Dadi" already built; Sarvam supports more Indian languages. |
| Legal / ethics? | Our own line only, no real person's voice or data; BNS 318/319 cited as applicable sections; legal review before any pilot; nothing is frozen automatically. |
| Why SQLite? | Zero-ops for a prototype; the schema (mule registry, sightings) moves to Postgres for a multi-agency deployment. |
| Business model / adoption? | Banks' fraud teams and state cyber cells as users; telcos host the honeypot numbers; reports map to NCRP fields. |

---

## Day-of checklist
- [ ] Laptop charged, charger, hotspot as backup internet
- [ ] Backend running; dashboard open on the HTTPS URL; auto-follow ticked
- [ ] Phone charged, volume up, Do Not Disturb on
- [ ] Gemini and Sarvam keys set; `/api/health` shows `gemini` and `sarvam`
- [ ] Backup video on the laptop desktop
- [ ] Repo public, deployment link and PPT link on a sticky note
