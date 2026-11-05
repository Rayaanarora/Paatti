"""Shared call service: session store, event hub, and the turn loop.

Every channel (dashboard text, browser phone, Twilio phone) goes through
CallService.respond, so they all get the same persona, extraction and live
dashboard updates.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque

from fastapi import HTTPException

from .config import Settings
from .extractor import LLMExtractor
from .persona import Persona
from .personas import resolve_persona
from .session import CallSession, RepeatRegistry

log = logging.getLogger(__name__)

CHANNELS = ("web", "web-phone", "phone")

# Identifier-bearing cues: digits, spoken digits, UPI '@', money words. Other turns are
# covered by the free rule extractor, which saves LLM quota.
_EXTRACT_CUES = re.compile(
    r"\d|@|\b(zero|one|two|three|four|five|six|seven|eight|nine|double|triple|onnu|rendu|moonu|naalu|anju|"
    r"ek|do|teen|char|paanch|lakh|lac|crore|rupees?|rs|ifsc|upi|account)\b", re.IGNORECASE)


def should_extract(session: CallSession) -> bool:
    scammer_turns = [t for t in session.turns if t.role == "scammer"]
    if not scammer_turns:
        return False
    return bool(_EXTRACT_CUES.search(scammer_turns[-1].text)) or len(scammer_turns) % 4 == 0


class Hub:
    """Fan-out of per-session events to every connected watcher.

    Thread- and loop-safe: each subscriber queue remembers its event loop and
    publishes are scheduled onto it, so watchers on any loop get every event.
    """

    def __init__(self) -> None:
        self._subs: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.setdefault(session_id, {})[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(session_id)
        if subs:
            subs.pop(q, None)
            if not subs:
                self._subs.pop(session_id, None)

    @staticmethod
    def _offer(q: asyncio.Queue, msg: dict) -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            log.warning("dropping event for a slow watcher")

    def publish(self, session_id: str, msg: dict) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for q, loop in list(self._subs.get(session_id, {}).items()):
            if loop is current:
                self._offer(q, msg)
            elif not loop.is_closed():
                loop.call_soon_threadsafe(self._offer, q, msg)

    def watchers(self, session_id: str) -> int:
        return len(self._subs.get(session_id, ()))


class SessionStore:
    def __init__(self, max_sessions: int, registry=None):
        self.registry = registry or RepeatRegistry()
        self._sessions: dict[str, CallSession] = {}
        self.max_sessions = max_sessions

    def create(self, *, persona_key: str = "paatti", channel: str = "web", caller_id: str | None = None) -> CallSession:
        if len(self._sessions) >= self.max_sessions:
            ended = [s for s in self._sessions.values() if s.ended_at is not None]
            if not ended:
                raise HTTPException(status_code=503, detail="too many live calls")  # never drop a live call
            victim = min(ended, key=lambda s: s.started_at)
            self._sessions.pop(victim.id, None)
        s = CallSession(self.registry, persona_key=persona_key, channel=channel, caller_id=caller_id)
        self._sessions[s.id] = s
        return s

    def get(self, session_id: str) -> CallSession:
        s = self._sessions.get(session_id)
        if not s:
            raise HTTPException(status_code=404, detail="session not found")
        return s

    def find(self, session_id: str) -> CallSession | None:
        return self._sessions.get(session_id)

    def all(self) -> list[CallSession]:
        return sorted(self._sessions.values(), key=lambda s: s.started_at, reverse=True)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise HTTPException(status_code=429, detail="too many requests, slow down")
        q.append(now)


class CallService:
    def __init__(self, settings: Settings, persona: Persona, extractor: LLMExtractor, store: SessionStore, hub: Hub,
                 db=None):
        self.db = db
        self.settings = settings
        self.persona = persona
        self.extractor = extractor
        self.store = store
        self.hub = hub
        self._tasks: set[asyncio.Task] = set()
        self._turn_locks: dict[str, asyncio.Lock] = {}

    def create_session(self, *, persona_key: str | None = None, channel: str = "web",
                       caller_id: str | None = None) -> CallSession:
        key = resolve_persona(persona_key, default=resolve_persona(self.settings.persona_key))
        s = self.store.create(persona_key=key, channel=channel if channel in CHANNELS else "web", caller_id=caller_id)
        self.persist(s)
        self.hub.publish("*", {"type": "session_created", "session": s.snapshot()})
        return s

    def persist(self, session: CallSession) -> None:
        if self.db is None:
            return
        try:
            self.db.save_session(session)
        except Exception:  # storage trouble must not drop a live call
            log.exception("could not persist session %s", session.id)

    def publish_state(self, session: CallSession) -> None:
        self.persist(session)
        self.hub.publish(session.id, {"type": "state", "state": session.snapshot()})

    def say(self, session: CallSession, text: str) -> dict:
        """Add a persona line that didn't come from a caller turn (greeting)."""
        with session.lock:
            turn = session.add_paatti_turn(text).model_dump(mode="json")
        self.hub.publish(session.id, {"type": "turn", "turn": turn})
        return turn

    async def respond(self, session: CallSession, text: str, *, background_extract: bool = True) -> dict:
        """One caller turn -> persona reply. Serialised per call so replies never interleave."""
        lock = self._turn_locks.setdefault(session.id, asyncio.Lock())
        async with lock:
            return await self._respond(session, text, background_extract)

    async def respond_stream(self, session: CallSession, text: str):
        """Voice version of respond(): yields the persona's reply sentence by sentence as it is
        generated, so speech can start before the whole reply exists. Records the turn at the end."""
        if len(session.turns) >= self.settings.max_turns_per_call:
            self.end(session)
            raise HTTPException(status_code=409, detail="call reached its turn limit")
        lock = self._turn_locks.setdefault(session.id, asyncio.Lock())
        async with lock:
            text = text.strip()[: self.settings.max_turn_chars]
            with session.lock:
                session.add_scammer_turn(text)
                scammer = session.turns[-1].model_dump(mode="json")
            self.hub.publish(session.id, {"type": "turn", "turn": scammer})
            self.publish_state(session)
            spoken: list[str] = []
            try:
                async for sentence in self.persona.reply_stream(session):
                    spoken.append(sentence)
                    yield sentence
            finally:
                reply = " ".join(spoken) or self.persona.scripted_reply(session)
                with session.lock:
                    paatti = session.add_paatti_turn(reply).model_dump(mode="json")
                self.hub.publish(session.id, {"type": "turn", "turn": paatti})
                self.persist(session)
                if self.extractor.enabled and should_extract(session):
                    self._spawn(self._extract(session))

    async def _respond(self, session: CallSession, text: str, background_extract: bool) -> dict:
        if len(session.turns) >= self.settings.max_turns_per_call:
            self.end(session)
            raise HTTPException(status_code=409, detail="call reached its turn limit")
        text = text.strip()[: self.settings.max_turn_chars]
        with session.lock:
            session.add_scammer_turn(text)
            scammer = session.turns[-1].model_dump(mode="json")
        self.hub.publish(session.id, {"type": "turn", "turn": scammer})
        self.publish_state(session)
        reply = await self.persona.reply(session)
        with session.lock:
            paatti = session.add_paatti_turn(reply).model_dump(mode="json")
        self.hub.publish(session.id, {"type": "turn", "turn": paatti})
        self.persist(session)
        if self.extractor.enabled and should_extract(session):
            if background_extract:
                self._spawn(self._extract(session))
            else:
                await self._extract(session)
        return paatti

    async def _extract(self, session: CallSession) -> None:
        try:
            if await self.extractor.update(session):
                self.publish_state(session)
        except Exception:  # background work must never kill a call
            log.exception("extraction failed")

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def end(self, session: CallSession) -> None:
        self._turn_locks.pop(session.id, None)
        session.end()
        self.publish_state(session)
        self.hub.publish(session.id, {"type": "ended"})
