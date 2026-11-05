"""Call session state: transcript, intel, stage tracking, read-back and hashing."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from typing import Callable

from .personas import get_profile
from .rule_extractor import extract_from_turn
from .schema import (
    LIST_FIELDS,
    READBACK_FIELDS,
    SINGLE_FIELDS,
    Finding,
    Intel,
    IntelField,
    Stage,
    StageEvent,
    Turn,
    TurnExtraction,
)
from .validators import (
    is_valid_account,
    is_valid_ifsc,
    is_valid_phone,
    is_valid_upi,
    readback_matches,
)

AFFIRM = re.compile(
    r"\b(yes|yeah|yep|correct|right|exactly|haan|han|ha|haanji|aama|aamam|sari|seri|ok|okay|confirm(ed)?)\b",
    re.IGNORECASE,
)
DENY = re.compile(r"\b(no|nahi|nahin|wrong|galat|illa|illai|not correct|incorrect)\b", re.IGNORECASE)

_VALIDATORS: dict[str, Callable[[str], bool]] = {
    "account_number": is_valid_account,
    "ifsc": is_valid_ifsc,
    "upi_id": is_valid_upi,
    "phone_number": is_valid_phone,
    "amount": lambda v: v.isdigit() and int(v) > 0,
    "claimed_agency": lambda v: 1 <= len(v) <= 80,
    "app_requested": lambda v: 1 <= len(v) <= 40,
}

REPEAT_TRACKED = ("account_number", "upi_id", "phone_number")
BASE_CONFIDENCE = {"rules": 0.7, "llm": 0.6, "rules+llm": 0.85}


class RepeatRegistry:
    """In-memory cross-call registry of mule identifiers (stretch goal: repeat match)."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], list[str]] = {}
        self._lock = threading.Lock()

    def lookup_and_add(self, field: str, value: str, session_id: str) -> list[str]:
        with self._lock:
            ids = self._seen.setdefault((field, value), [])
            prior = [i for i in ids if i != session_id]
            if session_id not in ids:
                ids.append(session_id)
            return prior


class CallSession:
    def __init__(
        self,
        registry: RepeatRegistry,
        clock: Callable[[], float] = time.time,
        *,
        persona_key: str = "paatti",
        channel: str = "web",
        caller_id: str | None = None,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.persona_key = persona_key
        self.channel = channel  # web (dashboard) | phone (Twilio) | web-phone (browser on a mobile)
        self.caller_id = caller_id  # network caller ID; may be spoofed
        self.dispatches: list[dict] = []
        self._clock = clock
        self.started_at = clock()
        self.ended_at: float | None = None
        self.registry = registry
        self.turns: list[Turn] = []
        self.intel = Intel()
        self.stage: Stage | None = None
        self.stage_history: list[StageEvent] = []
        self._audio = hashlib.sha256()
        self._audio_bytes = 0
        self._readback_offered: dict[str, str] = {}
        self.lock = threading.Lock()

    # ---- time -------------------------------------------------------------
    def now(self) -> float:
        return round(self._clock() - self.started_at, 2)

    def time_wasted(self) -> float:
        end = self.ended_at if self.ended_at is not None else self._clock()
        return round(end - self.started_at, 2)

    def end(self) -> None:
        if self.ended_at is None:
            self.ended_at = self._clock()

    # ---- audio ------------------------------------------------------------
    def add_audio_chunk(self, chunk: bytes) -> None:
        self._audio.update(chunk)
        self._audio_bytes += len(chunk)

    def audio_sha256(self) -> str | None:
        return self._audio.hexdigest() if self._audio_bytes else None

    # ---- turns ------------------------------------------------------------
    def add_scammer_turn(self, text: str) -> TurnExtraction:
        turn = Turn(index=len(self.turns), role="scammer", text=text, t=self.now())
        self.turns.append(turn)
        extraction = extract_from_turn(text, turn.index)
        self._resolve_readbacks(text, extraction.findings)
        self.merge_findings(extraction.findings, source="rules")
        if self.intel.claimed_agency is None and extraction.agency_mentions:
            self.merge_findings(extraction.agency_mentions[:1], source="rules")
        if extraction.stage:
            self.advance_stage(extraction.stage, turn.index)
        return extraction

    def add_paatti_turn(self, text: str) -> Turn:
        turn = Turn(index=len(self.turns), role="paatti", text=text, t=self.now())
        self.turns.append(turn)
        for field in READBACK_FIELDS:
            f: IntelField | None = getattr(self.intel, field)
            if f and not f.confirmed and _read_back(field, f.value, text):
                self._readback_offered[field] = f.value
        return turn

    # ---- stage ------------------------------------------------------------
    def advance_stage(self, stage: Stage, turn_index: int) -> bool:
        if self.stage is not None and stage.order <= self.stage.order:
            return False
        self.stage = stage
        self.stage_history.append(StageEvent(stage=stage, t=self.now(), turn_index=turn_index))
        return True

    # ---- read-back --------------------------------------------------------
    def pending_readback(self) -> tuple[str, str] | None:
        for field in READBACK_FIELDS:
            f: IntelField | None = getattr(self.intel, field)
            if f and not f.confirmed and self._readback_offered.get(field) != f.value:
                return field, f.value
        return None

    def readback_line(self, field: str, value: str) -> str:
        return get_profile(self.persona_key).readback_line(field, value)

    def _resolve_readbacks(self, text: str, findings: list[Finding]) -> None:
        for field, offered in list(self._readback_offered.items()):
            current: IntelField | None = getattr(self.intel, field)
            if not current or current.value != offered:
                self._readback_offered.pop(field, None)
                continue
            new_values = {f.value for f in findings if f.field == field}
            if new_values and offered not in new_values:
                continue  # correction; merge_findings replaces the value
            if offered in new_values or (AFFIRM.search(text) and not DENY.search(text)):
                current.confirmed = True
                current.confidence = self._confidence(current)
                self._readback_offered.pop(field, None)

    # ---- merging ----------------------------------------------------------
    def merge_findings(self, findings: list[Finding], source: str) -> int:
        """Validate and merge findings; returns how many were accepted."""
        accepted = 0
        for f in findings:
            if not self._valid(f):
                continue
            if f.field in SINGLE_FIELDS:
                accepted += self._merge_single(f, source)
            else:
                accepted += self._merge_list(f, source)
        return accepted

    def _valid(self, f: Finding) -> bool:
        if not 0 <= f.turn_index < len(self.turns):
            return False
        turn = self.turns[f.turn_index]
        # Evidence must be verbatim from a scammer turn: rejects hallucinated quotes.
        if turn.role != "scammer" or f.quote.strip() not in turn.text:
            return False
        return _VALIDATORS[f.field](f.value)

    def _new_field(self, f: Finding, source: str) -> IntelField:
        turn = self.turns[f.turn_index]
        field = IntelField(
            value=f.value, quote=f.quote.strip(), t=turn.t, turn_index=f.turn_index,
            confidence=0, source=source,
        )
        if f.field in REPEAT_TRACKED:
            field.repeat_sessions = self.registry.lookup_and_add(f.field, f.value, self.id)
        field.confidence = self._confidence(field)
        return field

    def _merge_single(self, f: Finding, source: str) -> int:
        existing: IntelField | None = getattr(self.intel, f.field)
        if existing and existing.value == f.value:
            if existing.source != source and existing.source != "rules+llm":
                existing.source = "rules+llm"
                existing.confidence = self._confidence(existing)
            return 0
        if existing and existing.confirmed and source == "llm":
            return 0  # never let the LLM overwrite a read-back-confirmed value
        if existing and f.turn_index < existing.turn_index:
            return 0  # keep the most recent statement
        setattr(self.intel, f.field, self._new_field(f, source))
        self._readback_offered.pop(f.field, None)
        return 1

    def _merge_list(self, f: Finding, source: str) -> int:
        items: list[IntelField] = getattr(self.intel, LIST_FIELDS[f.field])
        for item in items:
            if item.value == f.value:
                if item.source != source and item.source != "rules+llm":
                    item.source = "rules+llm"
                    item.confidence = self._confidence(item)
                return 0
        items.append(self._new_field(f, source))
        return 1

    @staticmethod
    def _confidence(f: IntelField) -> float:
        c = BASE_CONFIDENCE[f.source]
        if f.confirmed:
            c += 0.1
        c += 0.05 * min(len(f.repeat_sessions), 2)
        return round(min(c, 0.99), 2)

    # ---- serialisation ----------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "session_id": self.id,
            "persona": self.persona_key,
            "channel": self.channel,
            "caller_id": self.caller_id,
            "started_at": self.started_at,
            "time_wasted": self.time_wasted(),
            "ended": self.ended_at is not None,
            "stage": self.stage.value if self.stage else None,
            "stage_history": [e.model_dump(mode="json") for e in self.stage_history],
            "intel": self.intel.model_dump(mode="json"),
            "pending_readback": self.pending_readback(),
            "turn_count": len(self.turns),
            "first_capture_t": self.first_capture_t(),
            "dispatches": self.dispatches,
        }

    def first_capture_t(self) -> float | None:
        """Call-relative time the first mule identifier was captured (starts the golden hour)."""
        ts = [f.t for f in (self.intel.account_number, self.intel.upi_id, self.intel.ifsc) if f]
        return min(ts) if ts else None


def _read_back(field: str, value: str, text: str) -> bool:
    """Did Paatti's line read this identifier back? Ignores spacing and punctuation, so grouped
    read-backs ("H D F C, 0 0 0 1 2 3 4") count, but every character must be there in order."""
    if field == "account_number":
        return readback_matches(value, text)
    keep = (lambda t: re.sub(r"[^a-z0-9@.]", "", t.lower())) if field == "upi_id" else \
        (lambda t: re.sub(r"[^a-z0-9]", "", t.lower()))
    return keep(value) in keep(text)
