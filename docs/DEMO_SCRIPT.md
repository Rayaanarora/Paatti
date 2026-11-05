# Paatti — Live Demo Script

A rehearsed, about 90-second call. Paatti's replies are **pre-recorded when the server starts**, so they play instantly. Read-backs are generated from the digits she actually hears, so the evidence on screen is real.

**Roles**
- **Scammer:** a teammate, or a judge, reads the lines below, on the phone (QR call) or the dashboard mic.
- **Operator:** keeps the dashboard on the projector and points at what lights up.

**Before you start**
- **Demo mode:** `PAATTI_DEMO_MODE=1` (the default).
- **Persona:** Kamala Paatti (Tamil). Sharda Dadi (Hindi) has a matching script.
- **Warm-up:** open the backend URL about a minute early so the pre-recording finishes. On Render's free plan, the first request also wakes the server.
- **Fallback:** the dashboard has the same eight lines as one-click buttons ("Demo script lines"). If voice fails, click through them; it's the same pipeline in text mode.

---

| # | Scammer says (read naturally, firm and fast) | Paatti answers | Point at on the dashboard |
|---|---|---|---|
| 1 | "Hello, am I speaking with Kamala madam? This is Inspector Rajesh Verma from CBI, New Delhi." | "Haan, Kamala thaan pesaren. CBI-aa? Aiyo kanna, enna aachu? En paiyan nalla irukkaana?" | Stage **Hook**, agency card **CBI** |
| 2 | "Madam, your Aadhaar card is linked to a money-laundering case. Sixty-eight bank accounts are opened in your name." | "Aiyo Murugaa! Naan oru retired school teacher kanna, money laundering-na enna-nu kooda theriyaadhu. Please, enna pannanum-nu sollunga, naan cooperate panren." | Time-wasted counter running |
| 3 | "An arrest warrant is issued against you. This is a digital arrest. Do not disconnect and do not tell anyone." | "Arrest-aa? Kadavule! Seri seri, phone-a cut panna maatten, yaarukkum solla maatten. Neenga sonna maadhiri pannaren kanna." | Stages **Fear** → **Isolation** |
| 4 | "To clear your name, transfer Rs 4,50,000 to the RBI safe account. Note the number: **5021 0034 7781 9920**." | Reads it back digit by digit: "Account number 5 0 2 1 0 0 3 4 7 7 8 1 9 9 2 0. Sariya kanna?" | **Extraction**; amount **Rs 4,50,000**; account card **amber** (read-back pending) |
| 5 | "Yes, correct. The IFSC is **HDFC0001234**." | "IFSC H D F C 0 0 0 1 2 3 4. Sariya?" | Account card turns **green: LOCKED** (the demo moment) |
| 6 | "Correct. Transfer it immediately, the officer is waiting." | "Seri kanna, bank app open panren. Aiyo, idhu UPI ID kekkudhu. GPay-la anuppalaama? UPI ID sollunga, ippave anuppuren." | IFSC card **locked** |
| 7 | "Fine, send it by UPI to **rbi.verify@ybl**." | "UPI rbi.verify@ybl. Sariya?" | UPI card amber |
| 8 | "Yes! Do it now, madam, quickly." | "Seri kanna, anuppa poren. Oru nimisham, en paiyan phone pannaraan. Avan Chennai Cyber Crime-la thaan velai seyyaraan, avan kitta oru vaarthai kettuttu varen. Hello? Kanna? Line-la irukkeengala?" | UPI **locked**; the room laughs |

## The ending (about 20 seconds; rehearse it)

A real scammer who hears "Cyber Crime" panics and cuts the call. That is the ending.

1. **Pause** after Paatti's line 8 ("…Cyber Crime-la… Hello? Kanna? Line-la irukkeengala?"). Let the room react.
2. **The scammer hangs up.** On the phone: tap the red **Step 6 · Scammer panics** card (or the red End button). On the dashboard-only fallback: the red **"Scammer hears 'Cyber Crime' and hangs up"** button at the end of the demo lines. On a real Twilio call: just hang up.
3. **The phone** plays the three disconnect beeps and shows **"The scammer was kept busy 01:32"** with Account, IFSC and UPI ID each **Confirmed**.
4. **The laptop** shows the **Call ended** banner: time lost, the three identifiers with ticks, and **"Report downloaded to this laptop"**. The PDF downloads by itself; open it from the browser's download bar.
5. **Show the PDF:** the evidence quotes in the caller's own words, the stage timeline, BNS 318/319, and the SHA-256 hash.
6. **Close with the hand-off:** click **Report to 1930 / NCRP** in the report panel. The golden-hour timer and the reference number appear.

**Line to say:** "Ninety seconds of a scammer's time, three mule identifiers he confirmed himself, and a report ready for the bank before he finds his next victim."

The banner and the report stay on screen until you press **Close**. Scanning a new QR or pressing **Call again** on the phone will not replace them; the laptop moves to a new call only once someone is talking on it. Every ended call in the **Calls** list also has its own report download button.

On the phone, the script is the five lines of this table compressed (steps 1, 4, 5, 7, 8), then the hang-up card.

---

## Tips
- **Digits:** say them in groups of four ("five-zero-two-one, zero-zero-three-four…"). The read-back proves what was heard.
- **Wording:** a different phrasing is fine. Each step is matched by key words (CBI/inspector → Aadhaar/case → arrest/warrant → transfer/immediately → yes/do it). If something is fully off-script, Paatti answers live with Gemini, which takes a couple of seconds and plays one short filler.
- **Order:** the script only moves forward, so it never repeats a line.
- **Pace:** if Paatti sounds too slow or too fast, change only `SARVAM_TTS_PACE` on the server (default `2.0`, the fastest Bulbul v3 allows; try `1.6`–`1.9` for slower) and restart. The pre-recordings regenerate at the new pace.

## Sharda Dadi (Hindi) version
Same scammer lines, but say "Sharda madam" in line 1. Her replies, in order:
1. "Haan, Sharda bol rahi hoon. CBI? Arre beta, kya hua? Mera beta theek hai na?"
2. "Hai Ram! Main toh retired sarkari clerk hoon beta, money laundering kya hota hai mujhe pata bhi nahi. Batao kya karna hai, main poora saath dungi."
3. "Arrest? Hey Bhagwan! Theek hai, phone nahi kaatungi, kisi ko nahi bataungi. Jo bologe wahi karungi beta."
4. **Read-backs:** account, IFSC and UPI are generated from what she hears.
5. "Theek hai beta, bank ka app khol rahi hoon. Arre, yeh UPI ID maang raha hai. GPay se bhej doon? UPI ID batao, abhi bhejti hoon."
6. "Theek hai, bhej rahi hoon. Ek minute, mere bete ka phone aa raha hai. Woh Lucknow Cyber Crime mein kaam karta hai, usse ek baar pooch leti hoon. Hello? Beta? Line pe ho?"
