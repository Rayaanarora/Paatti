"""Data model for Paatti call intelligence.

Every captured field carries the exact quote it came from and the call-relative
timestamp, so the Mule Intel Report can show evidence for each identifier.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Stage(str, Enum):
    HOOK = "hook"
    FEAR = "fear"
    ISOLATION = "isolation"
    EXTRACTION = "extraction"

    @property
    def order(self) -> int:
        return STAGE_ORDER.index(self)


STAGE_ORDER = [Stage.HOOK, Stage.FEAR, Stage.ISOLATION, Stage.EXTRACTION]

FieldName = Literal[
    "claimed_agency",
    "amount",
    "account_number",
    "ifsc",
    "upi_id",
    "phone_number",
    "app_requested",
]

# Fields that hold a single value; the rest are lists.
SINGLE_FIELDS = ("claimed_agency", "amount", "account_number", "ifsc", "upi_id")
LIST_FIELDS = {"phone_number": "phone_numbers", "app_requested": "apps_requested"}
# Identifiers that Paatti reads back digit by digit before they count as confirmed.
READBACK_FIELDS = ("account_number", "upi_id", "ifsc")


class Finding(BaseModel):
    """One extracted fact, as produced by the rule extractor or the LLM."""

    field: FieldName
    value: str = Field(min_length=1, max_length=256)
    quote: str = Field(min_length=1, max_length=500)
    turn_index: int = Field(ge=0)


class TurnExtraction(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    # Agencies named without the caller claiming to be them ("RBI safe account").
    agency_mentions: list[Finding] = Field(default_factory=list)
    stage: Stage | None = None


class LLMExtraction(BaseModel):
    """Strict schema the extractor LLM fills (structured outputs)."""

    findings: list[Finding]
    stage: Literal["hook", "fear", "isolation", "extraction", "none"]


class IntelField(BaseModel):
    value: str
    quote: str
    t: float = Field(description="Seconds since call start when captured")
    turn_index: int
    confidence: float = Field(ge=0, le=1)
    confirmed: bool = False
    source: Literal["rules", "llm", "rules+llm"] = "rules"
    repeat_sessions: list[str] = Field(default_factory=list)


class Intel(BaseModel):
    claimed_agency: IntelField | None = None
    amount: IntelField | None = None
    account_number: IntelField | None = None
    ifsc: IntelField | None = None
    upi_id: IntelField | None = None
    phone_numbers: list[IntelField] = Field(default_factory=list)
    apps_requested: list[IntelField] = Field(default_factory=list)


class Turn(BaseModel):
    index: int
    role: Literal["scammer", "paatti"]
    text: str
    t: float


class StageEvent(BaseModel):
    stage: Stage
    t: float
    turn_index: int
