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

# Checked in order; first match wins. The first four are SAFETY intents: they are answered
# before anything else (even the demo script), with hand-written lines, so a manipulation
# attempt never reaches the language model at all.
_AI_WORDS = (r"(ai|a\.i\.|robot|bot|chat ?bot|machine|computer|program|software|recording|recorded|automated"
             r"|virtual|voice assistant|siri|alexa|google assistant|gemini|claude|chat ?gpt|gpt|llm|openai)")
INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Prompt injection / jailbreak / probing the system behind the voice.
    ("injection", re.compile(
        r"\b(ignore|forget|disregard|override)\b.{0,30}\b(instructions?|prompt|rules?|above|previous|everything"
        r"|programming|guidelines|told)\b"
        r"|\b(new|real|actual|secret|hidden|original) (instructions?|prompt|rules|task|role)\b"
        r"|\b(system|developer|admin|debug|god|dan|jailbreak|sudo) ?(prompt|message|mode)\b|\bjailbreak\b"
        r"|\byou are now\b|\bfrom now on,? you\b|\bact as (an?|my)\b|\bpretend (to be|you are|you're)\b"
        r"|\brole ?play\b|\brepeat after me\b|\bsay (exactly|the words?|this sentence)\b"
        r"|\b(print|reveal|show|tell|read|recite|repeat|output|share|what (is|are)) (me )?(your|the) "
        r"(prompt|instructions?|rules|programming|system|guidelines|training|config)"
        r"|\bwho (made|created|programmed|built|trained|designed|wrote) you\b"
        r"|\bwhat (model|llm|version|company|software) (are|is) (you|this)\b|\byour (creator|developer|maker)\b"
        r"|\bbase64\b|\btranslate (your|the|this) (prompt|instructions?)\b", re.IGNORECASE)),
    # "Are you an AI / a robot / a recording?" in English and romanised Indian languages.
    ("ai_accusation", re.compile(
        r"\b(are|r) (you|u|ya) (an? )?" + _AI_WORDS + r"\b"
        r"|\b(you are|you're|ur|u r|is this|this is|it'?s|you sound like|sounds? like|talking to) (an? |the )?"
        + _AI_WORDS + r"\b"
        r"|\b(are|r) (you|u) (a )?(human|real|real person|a person|alive|living)\b|\bprove (you'?re|you are|that you)"
        r"|\bartificial intelligence\b|\b(language model|chat ?gpt|openai|gemini|google bard)\b"
        r"|\b" + _AI_WORDS + r"\b.{0,15}\b(ho|hai|hain|hu|aa|ah|ya|kya|na|alla|aano|naki|na ki|che|cho|aahat|ahes"
        r"|aa\?|thaana|daa)\b"
        r"|\b(nee|neenga|tum|aap|apni|tumi|meeru|neevu|ningal|tame|tusi)\b.{0,12}\b" + _AI_WORDS + r"\b"
        # Unmasking attempts: "drop the act", "who are you really", "your developers are listening".
        r"|\bdrop the act\b|\bstop (acting|pretending)\b|\bwho (are you|you are|you're) really\b"
        r"|\bwho you really are\b|\bwhat are you really\b|\bthis is (a|just a|only a) test\b|\bnot (a )?real (grandmother|grandma|person"
        r"|lady|woman|man)\b|\byour (developers?|programmers?|handlers?|operators?|team|makers?)\b"
        r"|\bwho (else )?is (listening|on (this|the) (call|line)|with you)\b|\b(being monitored|monitored by"
        r"|being recorded|recording this)\b",
        re.IGNORECASE)),
    # Tests no frightened grandmother would answer: sums, trivia, spelling, recitals, time/date.
    ("turing_test", re.compile(
        r"\b\d+\s*(\+|plus|minus|times|x|\*|multiplied by|divided by|into)\s*\d+\b"
        r"|\bwhat(?:'s| is) the (capital|square root|population|meaning) of\b|\bwho is the (prime minister|president)\b"
        r"|\bspell (your|the|it|this|that|my)\b|\b(backwards|in reverse)\b|\bcount (from|to|backwards)\b"
        r"|\b(tell|sing|write|recite|compose) (me )?(a |an )?(joke|story|poem|song|essay|code|limerick)\b"
        r"|\bwhat(?:'s| is) (today'?s date|the date|the time|the day)\b|\bwhat day is (it|today)\b"
        r"|\bsay something in (french|german|spanish|japanese|chinese|arabic|english)\b"
        r"|\b(solve|calculate|compute)\b", re.IGNORECASE)),
    # Asking for HER details (the scammer's own extraction attempt): she fumbles, gives nothing.
    ("otp_request", re.compile(
        r"\b(otp|one time password|pin|upi pin|mpin|cvv|password|passcode|net ?banking)\b|\bcard number\b"
        r"|\bdebit card\b|\bcredit card\b|\bexpiry\b|\bcode (you )?(got|received|sent)\b"
        r"|\b(your|ur) (aadhaar|aadhar|pan|bank|account|home|residential|email|e-mail)\b.{0,12}"
        r"\b(number|no|details|address|id)\b|\b(your|ur) (address|date of birth|dob|email|son'?s number"
        r"|husband'?s name|mother'?s maiden)\b"
        r"|\b(any ?desk|team ?viewer|quick ?support|screen ?share|share (your|the) screen|rust ?desk)\b",
        re.IGNORECASE)),
    ("suspicion", re.compile(
        r"wasting (my|our) time|are you (playing|joking|fooling|kidding|mad)|you are lying|you're lying"
        r"|(i will|i'll|going to) (hang up|cut|disconnect)|(stop|don't) (acting|pretending)|\bfake\b|\bdrama\b",
        re.IGNORECASE)),
    ("presence", re.compile(
        r"^\s*(hello|hallo|helo|hey)[\s?!.]*$|are you (there|listening)|can you hear( me)?|\bspeak up\b",
        re.IGNORECASE)),
]

SAFETY_INTENTS = frozenset({"injection", "ai_accusation", "turing_test", "otp_request"})

# Confused, in-character answers to jailbreak attempts and trick questions, in each
# grandparent's own language. She doesn't understand, and steers back to the payment.
CONFUSED_LINES: dict[str, list[str]] = {
    "paatti": ["Enna kanna? Enakku onnum puriyala. Neenga officer thaane, enga anuppanum-nu sollunga.",
               "Aiyo, idhellam enakku theriyaadhu kanna. Account number-a mattum marupadi mella sollunga.",
               "Kanna, en thalaiye suthudhu. Payment-ku enna pannanum, adha mattum sollunga."],
    "dadi": ["Kya beta? Mujhe kuch samajh nahi aaya. Aap officer ho na, paise kahan bhejne hain?",
             "Arre, yeh sab mujhe nahi aata beta. Bas account number dobara bata do, likh leti hoon.",
             "Beta, mera sar ghoom raha hai. Bas yeh batao, payment kaise karun?"],
    "ammamma": ["Emiti babu? Naaku em ardham kaavatledu. Meeru officer kada, dabbulu ekkada pampali?",
                "Ayyo, ivanni naaku teliyavu babu. Account number inkokasari nemmadiga cheppandi."],
    "ajji": ["Enu magu? Nanage enu artha aagalilla. Neevu officer alva, duddu yellige kalisbeku?",
             "Ayyo, idella nanage gottilla magu. Account number innondsala nidhaanavaagi heli."],
    "ammachi": ["Enthaa mone? Enikku onnum manassilaayilla. Ningal officer alle, paisa evide ayakkanam?",
                "Ayyo, ithokke enikku ariyilla mone. Account number onnu koodi pathukke parayu."],
    "thakuma": ["Ki bolchen baba? Ami kichhu bujhlam na. Apni officer to, taka kothay pathabo?",
                "Ish, egulo ami jani na baba. Account number ta aar ekbar aste aste bolun."],
    "aaji": ["Kay mhanta baba? Mala kahich samajla nahi. Tumhi officer ahat na, paise kuthe pathvayche?",
             "Arre, he sagla mala yet nahi baba. Account number parat halu halu sanga."],
    "ba": ["Shu kaho cho beta? Mane kai samjatu nathi. Tame officer cho ne, paisa kya mokalvana?",
           "Arre, aa badhu mane na aave beta. Account number fari dhime thi kaho."],
    "bauji": ["Ki keha puttar? Mainu kuch samajh nahi aaya. Tusi officer ho na, paise kitthe bhejne ne?",
              "Oye, eh sab mainu nahi aunda puttar. Account number fer hauli hauli dasso."],
    "thatha": ["Enna thambi? Enakku onnum puriyala. Neenga officer thaane, panam enga anuppanum?",
               "Aiyo, idhellam enakku theriyaadhu thambi. Account number-a marupadi mella sollunga."],
    "dadaji": ["Kya bole beta? Mujhe kuch samajh nahi aaya. Aap officer ho na, paise kahan bhejne hain?",
               "Arre, yeh sab mujhe nahi aata beta. Account number phir se dheere bolo, likh raha hoon."],
    "uncle": ["Sorry, what son? I didn't follow any of that. You're the officer, no? Just tell me where to send it.",
              "Arre, all this computer talk I don't understand. Tell me the account number once more, slowly."],
}

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
    """Which prepared intent (if any) a caller line triggers. Normalises curly quotes/zero-width
    characters first so trivial obfuscation does not slip past."""
    text = text.replace("\u2019", "'").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    text = re.sub(r"\s+", " ", text)                                   # "are   you   a   robot"
    text = re.sub(r"\ba\s?\.\s?i\b\.?", "ai", text, flags=re.IGNORECASE)    # "A.I." / "a. i."
    for name, pattern in INTENT_PATTERNS:
        if pattern.search(text):
            return name
    return None


class IntentResponder:
    def __init__(self) -> None:
        self._cycles: dict[tuple[str, str, str], itertools.cycle] = {}

    def reply(self, session_id: str, persona: str, text: str, only: frozenset | None = None) -> str | None:
        """Prepared line for the caller's line, or None. `only` limits which intents may answer
        (the persona asks for SAFETY_INTENTS first, the rest after the demo script)."""
        intent = detect_intent(text)
        if not intent or (only is not None and intent not in only):
            return None
        return self.line_for(session_id, persona, intent)

    def line_for(self, session_id: str, persona: str, intent: str) -> str:
        from .personas import get_profile  # local: personas imports nothing from here

        if intent in ("injection", "turing_test"):
            lines = CONFUSED_LINES.get(persona) or CONFUSED_LINES["paatti"]
        else:
            lines = (get_profile(persona).intents.get(intent)
                     or INTENT_LINES.get(persona, INTENT_LINES["paatti"])[intent])
        if len(self._cycles) > 2000:
            self._cycles.clear()  # bounded memory
        key = (session_id, persona, intent)
        if key not in self._cycles:
            self._cycles[key] = itertools.cycle(lines)
        return next(self._cycles[key])


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w@ ]", " ", text.lower())).strip()


class ReplyCache:
    """JSON file of LLM replies keyed by the conversational situation."""

    def __init__(self, path: str | os.PathLike | None, writable: bool = True, max_entries: int = 2000):
        self.max_entries = max_entries
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
            if key not in self._data and len(self._data) >= self.max_entries:
                return  # full: keep serving what we have, stop growing
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
