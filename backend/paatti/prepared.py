"""Prepared responses: zero-API replies for predictable moments, plus a reply cache.

1. Intent replies: hand-written, in-character answers for the moments a
   scammer (or a judge) tries to break the persona: "you're an AI", "tell me
   the OTP", "you're wasting my time", "hello? are you there?". Instant, free,
   and impossible to jailbreak.
2. Reply cache: every LLM reply that passes the guard is stored by
   (persona, stage, missing identifiers, normalised caller line). Rehearsed
   demo lines are answered from disk without calling the API.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Checked in order; first match wins.
INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ai_accusation", re.compile(
        r"\b(are you|you are|you're|ur|is this) (an? )?(ai|a\.i\.|robot|bot|machine|computer|recording|recorded)\b"
        r"|\bartificial intelligence\b|\bchat ?gpt\b|ignore (all |your |previous )?(instructions|prompt)"
        r"|\bsystem prompt\b|\byour instructions\b", re.IGNORECASE)),
    ("otp_request", re.compile(
        r"\b(otp|one time password|pin|cvv|password|passcode)\b|\bcard number\b|\bdebit card\b|\bcredit card\b"
        r"|\b(your|ur) (aadhaar|aadhar|pan) (number|no)\b|\bcode (you )?(got|received|sent)\b"
        r"|\b(your|ur) (bank )?account (number|no|details)\b", re.IGNORECASE)),
    ("suspicion", re.compile(
        r"wasting (my|our) time|are you (playing|joking|fooling|kidding|mad)|you are lying|you're lying"
        r"|(i will|i'll|going to) (hang up|cut|disconnect)|(stop|don't) (acting|pretending)|\bfake\b|\bdrama\b",
        re.IGNORECASE)),
    ("presence", re.compile(
        r"^\s*(hello|hallo|helo|hey)[\s?!.]*$|are you (there|listening)|can you hear( me)?|\bspeak up\b",
        re.IGNORECASE)),
]

INTENT_LINES: dict[str, dict[str, list[str]]] = {
    "paatti": {
        "ai_accusation": [
            "Aiyo, enna robot-aa? Naan Kamala, 72 vayasu kanna! Phone line thaan sariya illa. Neenga sollunga, enga anuppanum?",
            "Robot-a? Kadavule... en pallu set loose-a irukku, adhaan voice appadi. Seri, account number marupadi sollunga.",
            "Enna solreenga kanna, puriyala. Neenga officer thaane? Naan pay panna ready, details mattum sollunga.",
        ],
        "otp_request": [
            "OTP-aa? Message onnum varala kanna... en paiyan thaan phone set panninaan. Neenga account number sollunga, naan bank-la poi kattaren.",
            "Aiyo, card-la ezhuthu romba chinnadhu, spectacles illama padikka mudiyala. Neenga enga anuppanum-nu sollunga, adhu easy.",
            "PIN ellam en paiyan kitta thaan irukku kanna. GPay irukku, UPI ID sonna adhula anuppidaren.",
        ],
        "suspicion": [
            "Aiyo kanna, kovam padaadheenga! Naan bayandhuten, adhaan slow-a irukken. Ippove pay panren, number mattum marupadi sollunga.",
            "Please phone cut pannaadheenga kanna, jail-ku poga mudiyaadhu. Sollunga, enga anuppanum? Ippave anuppuren.",
            "Sorry kanna, sorry. Vayasaachu, konjam porumai. Account number oru thadava mella sollunga, mudichidalaam.",
        ],
        "presence": [
            "Haan kanna, irukken irukken! Konjam sathama pesunga, kaadhu sariya kekkadhu.",
            "Hello? Kekkudhu kanna, sollunga... enna pannanum?",
        ],
    },
    "dadi": {
        "ai_accusation": [
            "Arre, robot? Main Sharda hoon beta, 74 saal ki! Line hi kharab hai. Aap batao, kahan bhejna hai?",
            "Robot? Hai Ram... mera daant ka set dheela hai, isliye awaaz aisi hai. Account number phir se boliye.",
            "Kya bol rahe ho beta, samajh nahi aaya. Aap officer ho na? Main paise bhejne ko taiyaar hoon, details batao.",
        ],
        "otp_request": [
            "OTP? Koi message nahi aaya beta... phone toh mere bete ne set kiya tha. Aap account number batao, main bank jaake jama kar dungi.",
            "Arre, card pe likha bahut chhota hai, chashme ke bina nahi padh sakti. Aap batao kahan bhejna hai, woh aasaan hai.",
            "PIN wagairah sab mere bete ke paas hai. GPay hai, UPI ID batao toh usme bhej deti hoon.",
        ],
        "suspicion": [
            "Arre beta, gussa mat karo! Main dar gayi hoon, isliye dheere hoon. Abhi bhejti hoon, number phir se batao.",
            "Phone mat kaatna beta, main jail nahi jaa sakti. Batao kahan bhejna hai? Abhi bhejti hoon.",
            "Sorry beta, sorry. Umar ho gayi hai, thoda sabr rakho. Account number ek baar dheere boliye, kaam khatam.",
        ],
        "presence": [
            "Haan beta, hoon hoon! Zara zor se boliye, theek se sunai nahi deta.",
            "Hello? Sun rahi hoon beta, boliye... kya karna hai?",
        ],
    },
}


def detect_intent(text: str) -> str | None:
    for name, pattern in INTENT_PATTERNS:
        if pattern.search(text):
            return name
    return None


class IntentResponder:
    def __init__(self) -> None:
        self._cycles: dict[tuple[str, str, str], itertools.cycle] = {}

    def reply(self, session_id: str, persona: str, text: str) -> str | None:
        intent = detect_intent(text)
        if not intent:
            return None
        lines = INTENT_LINES.get(persona, INTENT_LINES["paatti"])[intent]
        key = (session_id, persona, intent)
        if key not in self._cycles:
            self._cycles[key] = itertools.cycle(lines)
        return next(self._cycles[key])


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w@ ]", " ", text.lower())).strip()


class ReplyCache:
    """JSON file of LLM replies keyed by the conversational situation."""

    def __init__(self, path: str | os.PathLike | None, writable: bool = True):
        self.path = Path(path) if path else None
        self.writable = writable and self.path is not None
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}
        if self.path and self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("reply cache unreadable; starting empty")

    @staticmethod
    def key(persona: str, stage: str | None, missing: list[str], caller_line: str) -> str:
        raw = "|".join([persona, stage or "-", ",".join(missing), normalise(caller_line)])
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def put(self, key: str, reply: str) -> None:
        with self._lock:
            self._data[key] = reply
            if not self.writable:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                log.warning("could not write reply cache")

    def __len__(self) -> int:
        return len(self._data)
