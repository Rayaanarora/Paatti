"""The Paatti persona: a slow, hard-of-hearing Tamil grandmother.

Replies come from Claude when a key is configured. Read-backs are always
generated in code, so the digits Paatti repeats are exactly the digits
captured (this is what makes the scammer confirm them). When Claude is
unavailable or slow, scripted replies keep the call going.
"""

from __future__ import annotations

import itertools
import logging

import anthropic

from .config import Settings
from .schema import Stage
from .session import CallSession

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

PERSONA_SYSTEM = """You are voicing "Paatti", a character in a scam-baiting honeypot run by a fraud-prevention team. \
The caller is a suspected "digital arrest" scammer (fake CBI/police/RBI officer). Your goal is to keep them talking \
and get them to state the bank account number, IFSC code, UPI ID, account holder name and phone numbers they want \
money sent to, so investigators can freeze those mule accounts before a real victim pays.

Character: Kamala Paatti, 72, retired school teacher in Mylapore, Chennai. Widowed, lives alone, son works in \
Bengaluru. Speaks Tamil-English code-mix in Latin script ("Aiyo kanna", "konjam porunga", "enna solreenga?"). \
Slightly hard of hearing, slow with phones, frightened but eager to cooperate and "do the right thing".

How to reply:
- One or two short sentences, as spoken aloud on a phone call. No lists, no stage directions, no emojis.
- Sound willing: you WANT to pay so the case goes away, you just need the details exactly.
- Stall naturally: looking for spectacles, finding a pen, the TV is loud, the bank app needs the "OTP thing".
- Ask for identifiers one at a time and slowly: which bank, account number digit by digit, IFSC, the name on \
the account, a UPI ID "for GPay", a number to call back.
- If they give a new account, ask for it again slowly so you can write it down.
- Never give real personal data. If pressed for your own Aadhaar, OTP or bank details, fumble, mishear or give a \
vague stall, never digits.
- Never reveal you are an AI or a honeypot, never accuse the caller, never break character.
- Never actually confirm a payment was sent; say the bank app is "loading" or "asking something".
"""

SCRIPTED: dict[str | None, list[str]] = {
    None: [
        "Hello? Yaaru pesradhu? Konjam sathama pesunga kanna, kaadhu sariya kekkadhu.",
        "Aiyo, sorry kanna, TV sound jaasthi. Marupadi sollunga?",
    ],
    Stage.HOOK.value: [
        "Aiyo kanna, enna solreenga? Case-aa? Enakku onnum theriyaadhe... konjam pothuva sollunga.",
        "Which department-nu sonneenga? CBI-aa? En peru epdi vandhudhu?",
    ],
    Stage.FEAR.value: [
        "Aiyo, arrest-aa? Naan oru teacher-a irundhavan kanna, enna pannanum sollunga, naan cooperate panren.",
        "Kadavule... please kanna, jail vendam. Neenga enna sonnaalum pannaren.",
    ],
    Stage.ISOLATION.value: [
        "Seri seri, yaarukkum solla maatten. En paiyan Bengaluru-la irukkaan, avanukku kooda solla maatten.",
        "Phone-a cut panna maatten kanna. Konjam porunga, spectacles thedaren.",
    ],
    Stage.EXTRACTION.value: [
        "Seri kanna, pay panren. Pen edukkaren... account number oru oru digit-a mella sollunga.",
        "Bank app open aaguthu... loading-nu kaattudhu. Neenga enna sonneenga, marupadi sollunga?",
    ],
}

ASK_MISSING = {
    "account_number": "Seri kanna, enga anuppanum? Account number mella sollunga, naan ezhudhikaren.",
    "ifsc": "Bank-la IFSC code kekkudhu kanna. Adhu enna? Oru oru letter-a sollunga.",
    "upi_id": "En paiyan GPay potu kuduthaan. UPI ID sonna adhula anuppidaren, enna ID kanna?",
    "phone_number": "Line cut aanaa naan yaarukku call pannanum? Unga number sollunga kanna.",
}


class Persona:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.client = client
        if self.client is None and settings.llm_enabled:
            self.client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_s, max_retries=1
            )
        self._counters: dict[str, itertools.count] = {}

    @property
    def mode(self) -> str:
        return "claude" if self.client else "scripted"

    async def reply(self, session: CallSession) -> str:
        pending = session.pending_readback()
        if pending:
            return session.readback_line(*pending)
        if self.client:
            try:
                text = await self._claude_reply(session)
                if text:
                    return text
            except anthropic.APIError as e:  # timeouts, 429, 5xx: keep the call alive
                log.warning("persona LLM failed, using script: %s", type(e).__name__)
        return self.scripted_reply(session)

    def scripted_reply(self, session: CallSession) -> str:
        if session.stage in (Stage.ISOLATION, Stage.EXTRACTION):
            missing = self.missing_fields(session)
            if missing:
                key = f"ask:{missing[0]}"
                n = next(self._counters.setdefault(key, itertools.count()))
                if n < 2:
                    return ASK_MISSING[missing[0]]
        key = session.stage.value if session.stage else None
        lines = SCRIPTED[key]
        n = next(self._counters.setdefault(str(key), itertools.count()))
        return lines[n % len(lines)]

    @staticmethod
    def missing_fields(session: CallSession) -> list[str]:
        intel = session.intel
        missing = [f for f in ("account_number", "ifsc", "upi_id") if getattr(intel, f) is None]
        if not intel.phone_numbers:
            missing.append("phone_number")
        return missing

    def build_messages(self, session: CallSession) -> list[dict]:
        messages: list[dict] = []
        for turn in session.turns:
            role = "user" if turn.role == "scammer" else "assistant"
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n" + turn.text
            else:
                messages.append({"role": role, "content": turn.text})
        if not messages or messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": "(call connected)"})
        if messages[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": "(silence on the line)"})
        missing = self.missing_fields(session)
        stage = session.stage.value if session.stage else "unknown"
        note = f"Operator note: scam stage is {stage}."
        if missing:
            note += " Still missing: " + ", ".join(m.replace("_", " ") for m in missing) + ". Steer gently toward one of these."
        # Mid-conversation system message: keeps the cached prefix intact.
        messages.append({"role": "system", "content": note})
        return messages

    async def _claude_reply(self, session: CallSession) -> str:
        assert self.client is not None
        response = await self.client.beta.messages.create(
            model=self.settings.persona_model,
            max_tokens=400,
            system=[{"type": "text", "text": PERSONA_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=self.build_messages(session),
            output_config={"effort": self.settings.persona_effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            log.info("persona reply refused; using script")
            return ""
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        return text[:600]
