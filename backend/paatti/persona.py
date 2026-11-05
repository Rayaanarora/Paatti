"""The honeypot persona (Tamil Paatti by default; see personas.py for the pack).

Replies come from the configured LLM (Gemini or Claude). Read-backs are always
generated in code, so the digits repeated are exactly the digits captured.
Every LLM reply passes an output guard; anything that breaks character or
invents digits is replaced by a scripted line, as is any failed call.
"""

from __future__ import annotations

import itertools
import logging
import re

from .config import Settings
from .llm import LLMBackend, make_backend
from .personas import Profile, get_profile
from .demo import DemoDirector
from .prepared import IntentResponder, ReplyCache
from .speech import SentenceStreamer
from .schema import Stage
from .session import CallSession
from .validators import words_to_digits

log = logging.getLogger(__name__)

BREAKS_CHARACTER = re.compile(
    r"\b(AI|A\.I\.)\b|artificial intelligence|language model|chat ?bot|\bbot\b|\bassistant\b|honeypot|"
    r"\bprompt\b|my instructions|as an ai|i cannot|i can't help|i'm sorry, but",
    re.IGNORECASE,
)
DIGIT_RUN = re.compile(r"\d[\d ,\-]{2,}\d")
MAX_WORDS = 60


def guard_reply(text: str, session: CallSession) -> str | None:
    """Return a safe reply, or None if it must be replaced by a scripted line."""
    text = text.strip().strip('"').strip()
    if not text or BREAKS_CHARACTER.search(text):
        return None
    known = {re.sub(r"\D", "", f.value) for f in _captured(session)} | _spoken_digits(session)
    for m in DIGIT_RUN.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 4 and digits not in known:
            return None  # never let the model invent OTPs, PINs or account numbers
    words = text.split()
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS]).rstrip(",;") + "..."
    return text


def _spoken_digits(session: CallSession) -> set[str]:
    """Every digit run the scammer actually said (Paatti may repeat those, never invent new ones)."""
    out: set[str] = set()
    for t in session.turns:
        if t.role == "scammer":
            for m in DIGIT_RUN.finditer(words_to_digits(t.text)):
                out.add(re.sub(r"\D", "", m.group(0)))
    return out


def _captured(session: CallSession):
    intel = session.intel
    for name in ("amount", "account_number", "ifsc", "upi_id"):
        f = getattr(intel, name)
        if f:
            yield f
    yield from intel.phone_numbers


class Persona:
    def __init__(self, settings: Settings, backend: LLMBackend | None = None, *, use_backend: bool = True,
                 cache: ReplyCache | None = None):
        self.settings = settings
        self.backend = backend if backend is not None else (make_backend(settings) if use_backend else None)
        self.cache = cache if cache is not None else ReplyCache(settings.reply_cache_path, settings.reply_cache_write,
                                                                max_entries=settings.reply_cache_max)
        self.intents = IntentResponder()
        self.demo = DemoDirector() if settings.demo_mode else None
        self._counters: dict[str, itertools.count] = {}
        self.stats = {"readback": 0, "demo": 0, "intent": 0, "cache": 0, "llm": 0, "script": 0}

    @property
    def mode(self) -> str:
        return self.backend.name if self.backend else "scripted"

    def profile(self, session: CallSession) -> Profile:
        return get_profile(session.persona_key)

    async def reply(self, session: CallSession) -> str:
        """Cheapest safe source first: read-back, demo script, intent line, cache, LLM, script."""
        quick = self._quick_reply(session)
        if quick is not None:
            return quick
        if self.backend:
            profile = self.profile(session)
            raw = await self.backend.chat(profile.system_prompt, self.build_messages(session), self.operator_note(session))
            safe = guard_reply(raw, session) if raw else None
            if safe:
                self.stats["llm"] += 1
                self._remember(session, safe)
                return safe
            if raw:
                log.info("persona reply rejected by guard; using script")
        self.stats["script"] += 1
        return self.scripted_reply(session)

    async def reply_stream(self, session: CallSession):
        """Like reply(), but yields the reply sentence by sentence as soon as each is safe to say.

        Read-backs, intent lines, cached and scripted replies come out at once. An LLM reply
        streams; each sentence passes the output guard before it is released. If the first
        sentence fails the guard, a scripted line is used instead; if a later one fails, the
        reply stops there (what was already said was safe)."""
        stream_fn = getattr(self.backend, "chat_stream", None) if self.backend else None
        quick = self._quick_reply(session)
        if quick is not None:
            yield quick
            return
        if stream_fn is None:
            yield await self.reply(session)
            return
        profile = self.profile(session)
        splitter = SentenceStreamer()
        spoken: list[str] = []
        words = 0
        broke = False
        async for delta in stream_fn(profile.system_prompt, self.build_messages(session), self.operator_note(session)):
            for sentence in splitter.feed(delta):
                safe = guard_reply(sentence, session)
                if not safe:
                    broke = True
                    break
                spoken.append(safe)
                words += len(safe.split())
                yield safe
                if words >= MAX_WORDS:
                    broke = True
                    break
            if broke:
                break
        if not broke:
            for sentence in splitter.flush():
                safe = guard_reply(sentence, session)
                if safe:
                    spoken.append(safe)
                    yield safe
        if spoken:
            self.stats["llm"] += 1
            self._remember(session, " ".join(spoken))
        else:
            self.stats["script"] += 1
            yield self.scripted_reply(session)

    def _quick_reply(self, session: CallSession) -> str | None:
        """Read-back, intent line or cached reply: no model call needed."""
        pending = session.pending_readback()
        if pending:
            self.stats["readback"] += 1
            return session.readback_line(*pending)
        last = self._last_caller_line(session)
        scripted = self.demo.reply(session.id, session.persona_key, last) if self.demo and last else None
        if scripted:
            self.stats["demo"] += 1
            return scripted
        intent_line = self.intents.reply(session.id, session.persona_key, last) if last else None
        if intent_line:
            self.stats["intent"] += 1
            return intent_line
        key, cacheable, prev = self._cache_key(session)
        cached = self.cache.get(key) if cacheable else None
        if cached and cached != prev and guard_reply(cached, session):
            self.stats["cache"] += 1
            return cached
        return None

    @staticmethod
    def _last_caller_line(session: CallSession) -> str:
        return next((t.text for t in reversed(session.turns) if t.role == "scammer"), "")

    def _cache_key(self, session: CallSession) -> tuple[str, bool, str]:
        last = self._last_caller_line(session)
        prev = next((t.text for t in reversed(session.turns) if t.role == "paatti"), "")
        key = ReplyCache.key(session.persona_key, session.stage.value if session.stage else None,
                             self.missing_fields(session), f"{prev}\n{last}")
        return key, len(last.split()) >= 4, prev

    def _remember(self, session: CallSession, text: str) -> None:
        key, cacheable, _ = self._cache_key(session)
        if cacheable:
            self.cache.put(key, text)

    def scripted_reply(self, session: CallSession) -> str:
        profile = self.profile(session)
        if session.stage in (Stage.ISOLATION, Stage.EXTRACTION):
            missing = self.missing_fields(session)
            if missing:
                key = f"{session.id}:ask:{missing[0]}"
                if next(self._counters.setdefault(key, itertools.count())) < 2:
                    return profile.ask_missing[missing[0]]
        stage = session.stage.value if session.stage else None
        lines = profile.scripted[stage]
        n = next(self._counters.setdefault(f"{session.id}:{stage}", itertools.count()))
        return lines[n % len(lines)]

    @staticmethod
    def missing_fields(session: CallSession) -> list[str]:
        intel = session.intel
        missing = [f for f in ("account_number", "ifsc", "upi_id") if getattr(intel, f) is None]
        if not intel.phone_numbers:
            missing.append("phone_number")
        return missing

    def operator_note(self, session: CallSession) -> str:
        missing = self.missing_fields(session)
        stage = session.stage.value if session.stage else "unknown"
        note = f"Operator note: scam stage is {stage}."
        if missing:
            note += " Still missing: " + ", ".join(m.replace("_", " ") for m in missing)
            note += ". Steer gently toward one of these."
        return note

    @staticmethod
    def build_messages(session: CallSession) -> list[dict]:
        """Alternating user (caller) / assistant (persona) messages, starting and ending with user."""
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
        return messages
