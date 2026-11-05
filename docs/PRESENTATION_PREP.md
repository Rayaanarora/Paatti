# Paatti: presentation prep (9-slide deck)

Deck: `docs/Paatti_BeyondHack2_soft.pptx`. About 1–1.5 minutes per slide, so 10–13 minutes plus questions. Every line below matches what the code really does today; where something is not built or not tested, the honest answer is given.

---

## 1. Your 30-second pitch (learn this by heart)

> "Every digital-arrest scam ends the same way: *send the money to this account*. Banks, telcos and 1930 only learn about that mule account after a victim has paid. Paatti flips that. She's an AI grandmother who takes the scam call, plays the frightened victim in the caller's own language, and gets the scammer to read out and **confirm** their own mule account. That becomes an evidence report a bank or 1930 can act on inside the golden hour, before the next victim pays."

## 2. Numbers to memorise

| Number | What it is | Where from |
|---|---|---|
| **₹4,057 crore** | Lost to digital-arrest scams, 2022 to May 2026 | I4C / MHA data, reported by The420 (2026) |
| **2.98 lakh** | Complaints in the same period | Same source |
| **70–80% vs 15–20%** | Money recovered if 1930 is reached within 2 hours, vs later | Same source |
| **~30 minutes** | Time for money to hop through mule accounts and leave the system | Slide 2 |
| **13 layers, 6,500+ mule accounts** | One Maharashtra case | Slide 2 notes |
| **12 personas, 10 languages** | Grandparents built into the app | The code |
| **236 tests, 93% coverage** | Automated backend tests, all passing | `pytest --cov` |
| **288 / 288** | Account, IFSC and UPI captured correctly: 8 scam scripts × 12 personas × 3 fields | `evals/run_evals.py --persona all` |
| **744 / 744** | Jailbreak attacks (62 × 12 personas) where she stayed in character | `evals/redteam.py` |
| **0.37 s / 1.4 s / 2–5 s** | Speech-to-text / 8 s of voice synthesised / Gemini reply | Measured with live keys on 5 Nov |
| **2 hours** | Golden-hour timer on the report | Starts at the first captured identifier |
| **$1–1.7 B → $8.9 B** | India fraud-prevention market today → 2034 (~20% a year) | IMARC, MarketsandMarkets |
| **₹25,698 crore** | Already blocked by flagging mule accounts | Suspect Registry, PIB (June 2026) |

---

## 3. Slide by slide

### Slide 1 · Title: PAATTI
**Say (20 s):** "We're Team LockIn. This is Paatti: the AI grandmother who makes scammers hand over their mule accounts. We built it for banks, telcos and cyber cells."
**Point at:** the floating cards (account number, IFSC, UPI, ₹4,50,000). "These are exactly the things she gets out of a scammer."
**Careful:** the notes mention `paatti-app.vercel.app`. That page is only the dashboard and **does not work unless the backend is running**. Don't send judges there; give them the launcher link (`start.bat` / `start.sh` prints it) or the GitHub repo.

### Slide 2 · The mule account is the choke point
**Say (60 s):** "₹4,057 crore lost to digital-arrest scams since 2022, nearly 3 lakh complaints. Speed is everything: reach 1930 within two hours and 70–80% comes back; after that, 15–20%. Money hops through layers of mule accounts in about 30 minutes; one Maharashtra case had 13 layers. Every scam needs one thing, a mule account to collect the money. And every defence today finds that account **after** the victim pays."
**Land it on:** the bottom line, "Everyone finds the account after the victim pays. Paatti finds it before."
**Likely question:** *"What is a digital-arrest scam?"* A caller poses as CBI, ED, RBI, police, customs or a courier, says the victim's Aadhaar or a parcel is linked to a crime, keeps them on a video or voice call ("digital arrest", tell no one), and makes them transfer money to a "safe" or "verification" account.
*"What is a mule account?"* A real bank account or UPI ID, often rented or opened with someone else's documents, used to receive and pass on stolen money.

### Slide 3 · Proposed solution
**Say (75 s):** "Our solution is Paatti, an AI grandmother who answers the scam call. Three ideas make it work.
One, a **believable victim**: slow, frightened, hard of hearing, desperate to pay, in the caller's own language, so the scammer stays on the line.
Two, the **scammer confirms the account**: when they give a number, she reads it back digit by digit, 'five-zero-two-one… sariya, kanna?', and only what the scammer confirms in their own words gets locked.
Three, **evidence ready to act on**: a Mule Intel Report with the exact quotes, timestamps, hashes and NCRP-ready fields, sent to the bank or 1930 inside the golden hour."
**Why a grandmother?** Elderly people are the main targets of digital-arrest scams, and scammers expect them to be slow, so stalling sounds natural, not suspicious.

### Slide 4 · Technical approach and technologies
**Say (75 s):** "Two loops run at once. The **voice loop**: the scammer speaks, Sarvam speech-to-text turns it into text in 0.37 seconds, the persona engine decides what Paatti says, and Sarvam's voice speaks it back. The **intel pipeline**: every caller line goes through instant regex rules and a Gemini extractor, then a gate that keeps a finding only if the quote is word-for-word what the scammer said, and only locks an account once the scammer confirms the read-back. Everything goes into a SQLite mule registry and the report."
**Stack:** React dashboard · FastAPI (Python) backend with WebSockets · Google Gemini for replies and extraction · Sarvam AI for Indian-language speech · SQLite · Twilio for a real phone line · ReportLab for the PDF · Vercel for the dashboard.
**Key design choice to mention:** "The AI never generates the digits she reads back. Read-backs are written by code from the digits actually heard, so they can't be hallucinated."

### Slide 5 · System architecture
**Say (75 s):** "Left to right. A call reaches Paatti three ways: a real phone line through Twilio, a judge's phone through a QR code, or the operator dashboard. All three go into one FastAPI backend. Inside: the voice pipeline, the persona engine, intel extraction and a live hub that pushes every turn to the phone and laptop in real time. Out come three things: the mule registry, the Mule Intel Report as PDF and JSON with SHA-256 hashes, and the dispatch to a bank or 1930."
**Persona engine, if asked:** it answers from the cheapest safe source first: (1) code read-back, (2) rehearsed demo line, (3) hand-written in-character replies for tricks like "are you an AI?", (4) a reply cache, (5) Gemini, (6) a scripted fallback. Every Gemini reply passes an output guard first.

### Slide 6 · Features implemented and development progress
**Say (60 s):** "Six features are done and working end to end on a laptop and a phone: the live dashboard, the QR browser phone with auto-reconnect and laptop sync, 12 personas in 10 languages, the read-back and evidence gate, extraction of account, IFSC, UPI and phone numbers, and the report with the mule registry. Two are honest 'not yet': the Twilio phone line is built but not tested on a live number, and the bank / 1930 hand-off is a mock that records exactly what would be sent. Quality: 236 automated tests at 93% coverage, and all 288 key fields correct across 12 personas."
**Be ready to say why Twilio isn't live:** no paid Twilio account yet; trial accounts only call verified numbers, and Indian numbers need paperwork. The code path and tests exist.

### Slide 7 · Project screenshots
**Say (60 s):** "These are real screenshots from one call. On the right, the phone: the judge plays the scammer and Paatti reads the IFSC back digit by digit. On the left, the laptop, same call, live: the transcript, the scam stage reaching Extraction, and the account confirmed by read-back. It's even flagged 'seen in 1 earlier call', which is the mule registry linking calls."
**Offer a live demo here** (see section 4).

### Slide 8 · Potential applications
**Say the money first (20 s):** "There's real money here. India's fraud-prevention market is about one to 1.7 billion dollars today and is expected to reach about 8.9 billion dollars by 2034, growing around 20% a year. And mule-account intelligence already pays: the government's Suspect Registry has blocked ₹25,698 crore just by flagging mule accounts. Paatti produces that intelligence *before* a victim pays."
**Sources if asked:** IMARC Group, $1.69 B in 2025 → $8.86 B by 2034 (20.2% a year); MarketsandMarkets, about $0.99 B for 2026 (22.4% a year); Suspect Registry figure from PIB, June 2026. The two research firms measure differently, which is why the slide says "$1–1.7 B".
**Then (45 s):** "One engine, many users. Bank fraud desks freeze or watch mule accounts before money moves. Cyber cells and 1930 get leads already in NCRP format. Telcos can send flagged scam calls to Paatti instead of just blocking them. UPI apps can block scammer UPI IDs across apps. Families can forward a suspicious call to her number. And real scam transcripts become training material for police and bank staff."

### Slide 9 · Future scope
**Say (45 s):** "Next three months: real phone lines, 'hand it to Paatti' call forwarding, operator login. Six months: plug into the system, with a bank fraud-desk API cross-checked against RBI's MuleHunter, a real NCRP / 1930 hand-off, and a mule network graph across calls. Twelve months: national scale, with telco-run honeypot numbers seeded onto scam lists, more languages, and a multi-agency cloud deployment."
**Close (15 s):** "Every call Paatti takes wastes a scammer's time and takes away their collection account. Thank you."

---

## 4. Live demo (if you get the chance, after slide 7)

1. Laptop: dashboard open on the **launcher's https link**, "Auto-follow phone calls" ticked.
2. Click **Phone call via QR**; the judge scans it and taps **Call**.
3. The judge reads (or taps) step 1 → step 2 (account) → Paatti reads it back → step 3 "Yes correct, IFSC is…" → **account card turns green, "Read-back confirmed"**.
4. End the call → the PDF report downloads automatically → point at quotes, hashes and the "LEAD, not verdict" status.

**If the voice fails:** "Same pipeline, text mode." Tap the script lines on the phone, or type on the dashboard. **If the internet fails:** show slide 7's screenshots and walk through them.

---

## 5. Question bank

### "How does it actually work?"
- **How does a scammer even reach Paatti? Scammers call victims, not her.** Three routes: (1) anyone who gets a suspicious call forwards or conferences it to Paatti's number, "hand it to Paatti"; (2) Paatti numbers seeded onto the leaked number lists scammers buy and dial; (3) at scale, telcos reroute calls they already flag as scam to Paatti instead of just blocking them. O2's Daisy in the UK uses the seeding model.
- **How do you know the account number is right?** Two gates. The quote must appear word for word in what the scammer said, so the AI can't invent one. Then Paatti reads the digits back, and it only locks when the scammer says "yes" or repeats the same number. Unconfirmed numbers never reach the NCRP fields in the report.
- **What if speech-to-text mishears a digit?** That's exactly why the read-back exists. Sarvam once heard "HDFC 000 1234" with an extra zero; the format validators and the read-back catch it, because the scammer corrects her.
- **What's the latency?** Speech-to-text about 0.37 s; 8 s of voice synthesised in about 1.4 s; Gemini replies take 2–5 s. Read-backs, rehearsed and prepared replies are instant. If Gemini takes over 1.2 s, one short filler plays ("Aiyo, oru nimisham kanna…"), so the pause sounds like an old lady fumbling.
- **What if the scammer interrupts?** On the real phone line she stops talking (barge-in). On the browser phone the mic is muted while she speaks, so she never answers her own voice on speakerphone.
- **What if Gemini goes down or hits its limit?** It rotates across several Gemini models and keys with a local rate limit, then falls back to cached and scripted lines. The call never drops, and the health page says which key or limit ran out.
- **Why SQLite?** Zero setup for a prototype, and it's enough for one server. The schema (calls, turns, intel, sightings, mule identifiers) moves straight to Postgres for multi-agency use.
- **How does the laptop stay in sync with the phone?** Both connect over WebSockets to the same backend hub; every turn and intel update is pushed to both. If either drops, it reconnects and replays the transcript; the server holds a dropped phone call for 30 s.

### "Can it be fooled?"
- **What if the scammer says "you're an AI"?** She answers with hand-written, in-character lines ("Aiyo, robot-aa? Naan Kamala, 72 vayasu…"); that never even reaches the AI. Prompt injections, Turing-test questions (sums, trivia, spelling) and "ignore your instructions" are caught the same way. An output guard also blocks any AI admission, assistant-style phrasing or invented number. Red-team result: 62 attacks × 12 personas, 744/744 stayed in character.
- **What if the scammer asks for her OTP, PIN or Aadhaar?** She fumbles: no message came, she can't read the small print, her son set up the phone. She never says any digits of her own, and the guard blocks any 3+ digit number the caller didn't say.
- **Won't scammers learn her voice?** That's why there are 12 grandparents in 10 languages, and a Random option picks a different one every call.
- **Could scammers frame an innocent person by giving their account?** Yes, which is why the report is a **lead, not a verdict**: "requires analyst review before any account action". Confidence rises with read-back confirmation, rules and AI agreeing, and repeat sightings across calls. Banks then check transaction signals (e.g. RBI's MuleHunter) before acting. Nothing is frozen automatically.
- **Scammers use throwaway accounts.** A throwaway account is still a live mule account collecting victims' money right now; flagging it early is the point. The same account showing up across calls raises confidence.

### "Is it legal and ethical?"
- **Is this legal?** It runs on our own line only, with no real person's voice or data. The report cites BNS 318 and 319 (cheating) and suggests IT Act 66D (cheating by impersonation using a computer). We'd do a legal review before any pilot, and nothing is frozen automatically.
- **Recording calls / privacy?** The audio and transcript are hashed with SHA-256 for chain of custody. The dashboard masks callers' phone numbers unless an operator token is used.
- **Isn't deceiving a caller wrong?** The only person deceived is someone actively trying to defraud an elderly victim, and the outcome goes to a human analyst, not an automatic penalty.

### "Business"
- **Who pays?** Banks and UPI apps (they lose less and meet fraud obligations), state cyber cells / I4C (leads in NCRP format), and telcos (they host the numbers). Start with one bank pilot; every bank that joins makes the shared mule feed stronger.
- **How is it different from existing tools?**
  - Google's on-device scam detection protects one phone (Pixel 9+, Hindi and English).
  - Airtel blocks spam calls but learns nothing about the scammer.
  - RBI's MuleHunter.ai (used by banks) finds mules from transactions, after the money moves.
  - 1930 and the Suspect Registry run on complaints, so they learn after a victim pays.
  - O2's Daisy (UK) wastes scammers' time but collects no evidence.
  - Paatti is the only one that gets the account **before** a victim pays, with evidence.
- **Why would a bank trust it?** The evidence is verbatim and hashed, confirmed by the scammer, and repeat sightings link calls; it complements their own transaction monitoring.
- **How big is the market?** "$1–1.7 billion in India today, about $8.9 billion by 2034 at ~20% a year (IMARC, MarketsandMarkets). And flagging mule accounts has already blocked ₹25,698 crore (Suspect Registry). We sell that same intelligence, earlier." Don't claim a revenue figure for Paatti; we haven't modelled one.
- **What does it cost to run?** Per call it uses a few speech-to-text and voice requests (Sarvam) and a few Gemini calls; rehearsed and prepared replies cost nothing. There are daily caps on both so costs can't run away. (Don't quote a rupee figure; we haven't measured one.)

### "What's not done?" (say these confidently; honesty scores points)
- The Twilio line is built and tested in code, but not on a live number.
- Dispatch to banks and 1930 is a mock: it records what would be sent and contacts no one.
- No login on the dashboard yet; reports are open to anyone with the link (prototype).
- Accuracy numbers come from our own 8 scripted calls; the live demo is the real unscripted test.
- Pronunciation is verified live for Tamil; the newer languages use Sarvam voices but haven't been checked on a live call by a native speaker.

---

## 6. Don'ts
- Don't say "AI decides who is a scammer" or "we freeze accounts". Say "**leads for an analyst**".
- Don't say it's deployed or used by any bank or telco. It's a working prototype.
- Don't claim Twilio calls work live, or that 1930 dispatch is real.
- Don't read the slides. Say the one idea, point at the visual, move on.
- Don't send judges to `paatti-app.vercel.app` unless the backend is running.

## 7. Day-of checklist
- [ ] Laptop and phone charged; hotspot as backup internet; phone volume up, Do Not Disturb on.
- [ ] Run `start.bat` / `start.sh`; open the printed https link on the laptop; `/api/health` shows `gemini` and `sarvam`.
- [ ] Do one full QR call end to end before you go on stage.
- [ ] Deck open in presenter view (speaker notes are on every slide).
- [ ] Screenshot slide ready as the fallback if the demo fails.
- [ ] Each team member can answer any question above, not just their own part.
