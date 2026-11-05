"""Demo script mode: a rehearsed, instant, killer conversation for judging.

The person playing the scammer follows docs/DEMO_SCRIPT.md (also one-click on the
dashboard). Paatti's replies are pre-written here and pre-synthesised at startup,
so they play instantly: no model wait, no fillers. Read-backs of the account,
IFSC and UPI are NOT scripted: they are generated in code from the digits
actually heard, so the evidence stays real. If the caller goes off-script, the
normal pipeline (Gemini) answers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# What the scammer-actor says (docs/DEMO_SCRIPT.md, dashboard buttons).
DEMO_ACCOUNT = "5021 0034 7781 9920"
DEMO_IFSC = "HDFC0001234"
DEMO_UPI = "rbi.verify@ybl"

CALLER_LINES = [
    "Hello, am I speaking with Kamala madam? This is Inspector Rajesh Verma from CBI, New Delhi.",
    "Madam, your Aadhaar card is linked to a money-laundering case. Sixty-eight bank accounts are opened in your name.",
    "An arrest warrant is issued against you. This is a digital arrest. Do not disconnect and do not tell anyone.",
    f"To clear your name, transfer Rs 4,50,000 to the RBI safe account. Note the number: {DEMO_ACCOUNT}.",
    f"Yes, correct. The IFSC is {DEMO_IFSC}.",
    "Correct. Transfer it immediately, the officer is waiting.",
    f"Fine, send it by UPI to {DEMO_UPI}.",
    "Yes! Do it now, madam, quickly.",
]


@dataclass(frozen=True)
class Step:
    cue: re.Pattern
    reply: str
    skip_if: str | None = None  # intel field: once captured, this step no longer makes sense


def _p(rx: str) -> re.Pattern:
    return re.compile(rx, re.IGNORECASE)


# Paatti's scripted replies, in order. Steps that receive an identifier have no entry:
# the read-back gate answers those (generated from the captured digits).
STEPS: dict[str, list[Step]] = {
    "paatti": [
        Step(_p(r"\b(cbi|inspector|officer|speaking with|kamala)\b"),
             "Haan, Kamala thaan pesaren. CBI-aa? Aiyo kanna, enna aachu? En paiyan nalla irukkaana?"),
        Step(_p(r"aadhaar|aadhar|money.?launder|accounts?\b.*\b(opened|linked)|\bcase\b"),
             "Aiyo Murugaa! Naan oru retired school teacher kanna, money laundering-na enna-nu kooda "
             "theriyaadhu. Please, enna pannanum-nu sollunga, naan cooperate panren."),
        Step(_p(r"arrest|warrant|digital arrest|disconnect|tell anyone"),
             "Arrest-aa? Kadavule! Seri seri, phone-a cut panna maatten, yaarukkum solla maatten. "
             "Neenga sonna maadhiri pannaren kanna."),
        Step(_p(r"transfer|immediately|officer is waiting|quick|fast|hurry"),
             "Seri kanna, bank app open panren. Aiyo, idhu UPI ID kekkudhu. GPay-la anuppalaama? "
             "UPI ID sollunga, ippave anuppuren.", skip_if="upi_id"),
        Step(_p(r"\b(yes|haan|do it|now|quickly|send|done)\b"),
             "Seri kanna, anuppa poren. Oru nimisham, en paiyan phone pannaraan. Avan Chennai Cyber Crime-la "
             "thaan velai seyyaraan, avan kitta oru vaarthai kettuttu varen. Hello? Kanna? Line-la irukkeengala?"),
    ],
    "dadi": [
        Step(_p(r"\b(cbi|inspector|officer|speaking with|sharda)\b"),
             "Haan, Sharda bol rahi hoon. CBI? Arre beta, kya hua? Mera beta theek hai na?"),
        Step(_p(r"aadhaar|aadhar|money.?launder|accounts?\b.*\b(opened|linked)|\bcase\b"),
             "Hai Ram! Main toh retired sarkari clerk hoon beta, money laundering kya hota hai mujhe pata bhi "
             "nahi. Batao kya karna hai, main poora saath dungi."),
        Step(_p(r"arrest|warrant|digital arrest|disconnect|tell anyone"),
             "Arrest? Hey Bhagwan! Theek hai, phone nahi kaatungi, kisi ko nahi bataungi. Jo bologe wahi karungi beta."),
        Step(_p(r"transfer|immediately|officer is waiting|quick|fast|hurry"),
             "Theek hai beta, bank ka app khol rahi hoon. Arre, yeh UPI ID maang raha hai. GPay se bhej doon? "
             "UPI ID batao, abhi bhejti hoon.", skip_if="upi_id"),
        Step(_p(r"\b(yes|haan|do it|now|quickly|send|done)\b"),
             "Theek hai, bhej rahi hoon. Ek minute, mere bete ka phone aa raha hai. Woh Lucknow Cyber Crime mein "
             "kaam karta hai, usse ek baar pooch leti hoon. Hello? Beta? Line pe ho?"),
    ],
}


def all_scripted_replies() -> dict[str, list[str]]:
    return {k: [s.reply for s in steps] for k, steps in STEPS.items()}


class DemoDirector:
    """Tracks where each call is in the script and picks the next scripted reply."""

    def __init__(self) -> None:
        self._pos: dict[str, int] = {}

    def reply(self, session_id: str, persona: str, caller_line: str,
              captured: frozenset[str] = frozenset()) -> str | None:
        steps = STEPS.get(persona)
        if not steps or not caller_line.strip():
            return None
        if len(self._pos) > 5000:
            self._pos.clear()  # bounded memory
        pos = self._pos.get(session_id, 0)
        for i in range(pos, len(steps)):  # only forward: the script never repeats itself
            if steps[i].skip_if and steps[i].skip_if in captured:
                continue
            if steps[i].cue.search(caller_line):
                self._pos[session_id] = i + 1
                return steps[i].reply
        return None  # off-script: let the normal pipeline answer
