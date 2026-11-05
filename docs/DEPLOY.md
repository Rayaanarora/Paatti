# Deploy and demo setup

There are three ways to run Paatti. Pick based on time and what you have.

| Option | What you get | Time |
|---|---|---|
| **A. Laptop + tunnel** (recommended at the venue) | Public HTTPS link from your laptop; phone QR demo works; Twilio can reach you | 5 min |
| **B. Render (cloud)** | Permanent public link for judges (Round II #15) | 10–15 min |
| **C. Laptop only** | Dashboard on localhost; text and laptop-mic demo | 2 min |

**Never commit keys.** Put them in environment variables or the hosting dashboard.

---

## A. Laptop + tunnel

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

In a second terminal, open a public HTTPS tunnel (either one):

```bash
cloudflared tunnel --url http://localhost:8000
```

```bash
ngrok http 8000
```

Copy the `https://…` URL it prints, then restart the backend with it so QR codes and Twilio use it:

```bash
export PAATTI_PUBLIC_URL=https://your-tunnel-url
cd backend
uvicorn paatti.main:app --port 8000
```

Open the **tunnel URL** on the projector laptop (not localhost), so the QR code points phones at an HTTPS address. Phones only allow the microphone over HTTPS.

## B. Render (cloud)

1. Push the repo to GitHub (public, for Round III #19).
2. Render dashboard → **New → Blueprint** → pick the repo. `render.yaml` sets up one Docker web service.
3. In the service's **Environment** tab, set `GEMINI_API_KEY`, `SARVAM_API_KEY`, and `PAATTI_PUBLIC_URL` (the `https://….onrender.com` URL Render gives you).
4. Open `https://….onrender.com/api/health` and check `"status": "ok"`.

Notes:
- **Cold starts:** the free plan sleeps when idle and the first request takes about a minute, so open the link before you present.
- **Database resets:** the free plan's disk is wiped on each deploy, so SQLite starts fresh. That's fine for a demo.
- **Docker build untested here:** the Dockerfile builds the dashboard and serves it from the API, but it couldn't be test-built in our build environment (Docker Hub rate-limited it). Build it locally once to confirm before relying on it:

```bash
docker build -t paatti .
```

```bash
docker run -p 8000:8000 -e GEMINI_API_KEY=your-key paatti
```

## C. Laptop only
Same as A without the tunnel. Open http://localhost:8000. Text mode and laptop-mic browser voice work; the phone QR needs HTTPS (option A or B).

---

## Real phone calls (Twilio)

1. Create a Twilio account and buy a voice-capable number. Trial accounts play a short trial message and can only call **verified** numbers, so verify the demo phone in the Twilio console.
2. Set these, then restart the backend:

```bash
export TWILIO_ACCOUNT_SID=ACxxxxxxxx
export TWILIO_AUTH_TOKEN=your-auth-token
export TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
export PAATTI_PUBLIC_URL=https://your-tunnel-or-render-url
export PAATTI_OPERATOR_TOKEN=pick-a-long-random-string
export PAATTI_OUTBOUND_ALLOWLIST=+91XXXXXXXXXX
```

3. In the Twilio console → your number → **Voice → A call comes in → Webhook**, enter `https://your-public-url/telephony/twilio/voice` (HTTP POST).
4. **Inbound:** dial the Twilio number from any phone and Paatti answers.
5. **Outbound ("ring the judge's phone"):** in the dashboard's Phone demo panel, enter an allowlisted number and the operator token, then click **Ring**.

Security: webhooks are rejected unless Twilio's signature matches; each audio stream needs a one-time token; outbound calls need the operator token and an allowlisted number.

**Untested:** the Twilio and Sarvam message formats were written from their public API documentation and tested against fakes; they have not been run against the live services. Make one test call before the demo.

---

## Keeping Gemini under its limits

Already built in, nothing to do:
- **Model chain:** `PAATTI_GEMINI_MODELS` rotates on 404/429/503. Add more keys with `GEMINI_API_KEYS=key1,key2` to spread quota.
- **Local cap:** `PAATTI_GEMINI_RPM` (default 8 per model per key) stays under free-tier rate limits.
- **Daily cap:** `PAATTI_GEMINI_DAILY_CAP` (default 600). After it, Paatti uses scripted lines.
- **Free paths first:** intent replies and the reply cache answer predictable lines without any API call; the LLM extractor only runs on turns with digits, `@` or money words.
- **Live status:** `GET /api/health` shows `reply_sources` (how many replies came from read-back, intent, cache, LLM or script) and `llm_pool` (each model's state).

## Pre-demo checklist
- [ ] `/api/health` → status ok, persona `gemini`, voice `sarvam`
- [ ] Dashboard opened via the HTTPS URL; "Auto-follow phone calls" ticked
- [ ] One full rehearsal call (QR or Twilio) done today
- [ ] Phone volume up, not on silent; Do Not Disturb on for notifications
- [ ] Fallback ready: dashboard text mode and the backup video
