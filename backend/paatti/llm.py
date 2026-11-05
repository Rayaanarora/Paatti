"""LLM backends: Gemini (default) and Claude, behind one small interface.

Both return None on any failure so the call falls back to scripted replies and
the rule extractor. Extraction uses a loose schema; Finding validation happens
per item in extractor.py so one bad item doesn't discard the rest.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel

from .config import Settings

log = logging.getLogger(__name__)

ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LooseFinding(BaseModel):
    field: Literal[
        "claimed_agency", "amount", "account_number", "ifsc", "upi_id", "phone_number", "app_requested"
    ]
    value: str
    quote: str
    turn_index: int


class LooseExtraction(BaseModel):
    findings: list[LooseFinding]
    stage: Literal["hook", "fear", "isolation", "extraction", "none"]


class LLMBackend(Protocol):
    name: str

    async def chat(self, system: str, messages: list[dict], note: str | None) -> str | None: ...

    async def extract(self, system: str, transcript: str) -> LooseExtraction | None: ...


@dataclass
class Slot:
    key_index: int
    model: str
    cooldown_until: float = 0.0
    dead: bool = False
    no_thinking: bool = False
    hits: deque = field(default_factory=deque)


class GeminiPool:
    """Tracks every (key, model) pair so we rotate instead of hitting limits.

    - local requests-per-minute cap per pair keeps us under free-tier limits
    - 429 / 5xx / timeouts put a pair on cooldown; 404 retires a model; 401/403 retires a key
    - a daily cap switches the app to scripted replies instead of erroring
    """

    def __init__(self, n_keys: int, rpm: int, daily_cap: int, clock=time.monotonic, today=None):
        self.n_keys = n_keys
        self.rpm = rpm
        self.daily_cap = daily_cap
        self.clock = clock
        self.today = today or (lambda: time.strftime("%Y-%m-%d"))
        self._slots: dict[tuple[int, str], Slot] = {}
        self._day = self.today()
        self.calls_today = 0

    def slot(self, key_index: int, model: str) -> Slot:
        return self._slots.setdefault((key_index, model), Slot(key_index, model))

    def _rpm_ok(self, s: Slot, now: float) -> bool:
        while s.hits and now - s.hits[0] > 60:
            s.hits.popleft()
        return len(s.hits) < self.rpm

    def budget_left(self) -> bool:
        if self.today() != self._day:
            self._day, self.calls_today = self.today(), 0
        return self.calls_today < self.daily_cap

    def candidates(self, models: list[str]) -> list[Slot]:
        now = self.clock()
        return [
            s for m in models for k in range(self.n_keys)
            if not (s := self.slot(k, m)).dead and s.cooldown_until <= now and self._rpm_ok(s, now)
        ]

    def used(self, s: Slot) -> None:
        s.hits.append(self.clock())
        self.calls_today += 1

    def cool(self, s: Slot, seconds: float) -> None:
        s.cooldown_until = self.clock() + seconds

    def retire_key(self, key_index: int) -> None:
        for (k, _), s in self._slots.items():
            if k == key_index:
                s.dead = True

    def status(self) -> list[dict]:
        now = self.clock()
        return [
            {"key": k, "model": m, "state": "retired" if s.dead else
             ("cooling" if s.cooldown_until > now else "ready"), "rpm_used": len(s.hits)}
            for (k, m), s in self._slots.items()
        ]


class GeminiBackend:
    name = "gemini"

    def __init__(self, settings: Settings, clients: list | None = None, pool: GeminiPool | None = None):
        self.settings = settings
        if clients is None:
            from google import genai

            clients = [genai.Client(api_key=k) for k in settings.gemini_api_keys]
        self.clients = clients
        self.pool = pool or GeminiPool(len(clients), settings.gemini_rpm, settings.gemini_daily_cap)
        for k in range(len(clients)):  # register slots so status() shows the whole chain
            for m in dict.fromkeys(settings.gemini_models + settings.gemini_extractor_models):
                self.pool.slot(k, m)

    def _config(self, slot: Slot, system: str, **extra):
        from google.genai import types

        kw = dict(system_instruction=system, **extra)
        if not slot.no_thinking:
            if "2.5" in slot.model:
                kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            elif self.settings.gemini_thinking_level:
                kw["thinking_config"] = types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel(self.settings.gemini_thinking_level.upper()))
        return types.GenerateContentConfig(**kw)

    async def _generate(self, models: list[str], contents, system: str, timeout: float, **extra):
        from google.genai import errors as genai_errors

        if not self.pool.budget_left():
            log.warning("gemini daily cap reached; using scripted replies")
            return None
        deadline = time.monotonic() + timeout
        attempts = 0
        queue = self.pool.candidates(models)
        while queue and attempts < 4:
            slot = queue.pop(0)
            remaining = deadline - time.monotonic()
            if remaining < 0.3:
                break
            attempts += 1
            self.pool.used(slot)
            try:
                return await asyncio.wait_for(
                    self.clients[slot.key_index].aio.models.generate_content(
                        model=slot.model, contents=contents, config=self._config(slot, system, **extra)),
                    timeout=remaining,
                )
            except genai_errors.APIError as e:
                code = getattr(e, "code", 0) or 0
                log.warning("gemini %s failed with %s; rotating", slot.model, code)
                if code == 429:
                    self.pool.cool(slot, 60)
                elif code >= 500:
                    self.pool.cool(slot, 20)
                elif code == 404:
                    slot.dead = True
                elif code in (401, 403):
                    self.pool.retire_key(slot.key_index)
                elif code == 400 and not slot.no_thinking and "think" in str(e).lower():
                    slot.no_thinking = True  # model doesn't take this thinking setting; retry without
                    queue.insert(0, slot)
                else:
                    return None  # malformed request: switching models won't help
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                log.warning("gemini %s %s; rotating", slot.model, type(e).__name__)
                self.pool.cool(slot, 10)
        return None

    async def chat(self, system: str, messages: list[dict], note: str | None) -> str | None:
        from google.genai import types

        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
            for m in messages
        ]
        prompt = system + (f"\n\n{note}" if note else "")
        resp = await self._generate(self.settings.gemini_models, contents, prompt, self.settings.llm_timeout_s,
                                    max_output_tokens=300)
        text = (resp.text or "").strip() if resp is not None else ""
        return text or None

    async def extract(self, system: str, transcript: str) -> LooseExtraction | None:
        resp = await self._generate(self.settings.gemini_extractor_models, transcript, system, 30,
                                    response_mime_type="application/json", response_schema=LooseExtraction)
        if resp is None:
            return None
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, LooseExtraction):
            return parsed
        try:
            return LooseExtraction.model_validate_json(resp.text or "")
        except ValueError:
            log.warning("gemini extraction was not valid JSON for the schema")
            return None

    def status(self) -> dict:
        return {"calls_today": self.pool.calls_today, "daily_cap": self.pool.daily_cap, "slots": self.pool.status()}


class AnthropicBackend:
    name = "claude"

    def __init__(self, settings: Settings, client=None):
        import anthropic

        self.settings = settings
        self._errors = (anthropic.APIError,)
        self.client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_s, max_retries=1
        )

    async def chat(self, system: str, messages: list[dict], note: str | None) -> str | None:
        msgs = list(messages)
        if note:
            # Mid-conversation system message keeps the cached prefix intact.
            msgs.append({"role": "system", "content": note})
        try:
            resp = await self.client.beta.messages.create(
                model=self.settings.persona_model,
                max_tokens=400,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=msgs,
                output_config={"effort": self.settings.persona_effort},
                betas=[ANTHROPIC_FALLBACK_BETA],
                fallbacks="default",
            )
        except self._errors as e:
            log.warning("claude chat failed: %s", type(e).__name__)
            return None
        if resp.stop_reason == "refusal":
            return None
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None

    async def extract(self, system: str, transcript: str) -> LooseExtraction | None:
        try:
            resp = await self.client.beta.messages.parse(
                model=self.settings.extractor_model,
                max_tokens=8000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": transcript}],
                output_format=LooseExtraction,
                betas=[ANTHROPIC_FALLBACK_BETA],
                fallbacks="default",
            )
        except self._errors as e:
            log.warning("claude extraction failed: %s", type(e).__name__)
            return None
        if resp.stop_reason in ("refusal", "max_tokens"):
            return None
        return resp.parsed_output


def make_backend(settings: Settings) -> LLMBackend | None:
    provider = settings.llm_provider
    if provider == "gemini":
        return GeminiBackend(settings)
    if provider == "anthropic":
        return AnthropicBackend(settings)
    return None
