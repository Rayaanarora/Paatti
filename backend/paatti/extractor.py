"""LLM extractor: Claude fills a strict JSON schema over the rolling transcript.

Its findings go through the same validators as the rule extractor, and every
quote must appear verbatim in the cited scammer turn, so a hallucinated
identifier cannot reach the report.
"""

from __future__ import annotations

import logging

import anthropic

from .config import Settings
from .schema import LLMExtraction, Stage
from .session import CallSession

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

EXTRACTOR_SYSTEM = """You extract intelligence from transcripts of suspected "digital arrest" scam calls for a \
bank fraud team. The transcript lines are numbered [turn_index] and labelled SCAMMER or PAATTI (the honeypot).

Extract only what the SCAMMER said. For each fact, return a finding with:
- field: one of claimed_agency, amount, account_number, ifsc, upi_id, phone_number, app_requested
- value: normalised value (account/phone: digits only; amount: whole rupees as digits, e.g. "4.5 lakh" -> "450000"; \
ifsc: uppercase; upi_id: as spoken, e.g. name@bank; app_requested: product name such as Skype or WhatsApp; \
claimed_agency: short name such as CBI, RBI, Mumbai Police)
- quote: an exact, verbatim substring copied from that SCAMMER line (no paraphrase, no added punctuation)
- turn_index: the number of the line the quote comes from

Convert spoken digits ("double five", "onnu rendu") to numerals in value, but keep the quote verbatim. \
If the scammer corrects a value, report the corrected one from the later turn. Do not guess or invent values. \
Also return the furthest scam stage reached: hook (pretext such as a parcel or case), fear (arrest, warrant), \
isolation (stay on call, tell no one), extraction (demand to pay or transfer), or none."""


def render_transcript(session: CallSession, last_n: int = 40) -> str:
    lines = []
    for turn in session.turns[-last_n:]:
        who = "SCAMMER" if turn.role == "scammer" else "PAATTI"
        lines.append(f"[{turn.index}] {who}: {turn.text}")
    return "\n".join(lines)


class LLMExtractor:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.client = client
        if self.client is None and settings.llm_enabled:
            self.client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key, timeout=30, max_retries=1
            )

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def extract(self, session: CallSession) -> LLMExtraction | None:
        if not self.client or not session.turns:
            return None
        try:
            response = await self.client.beta.messages.parse(
                model=self.settings.extractor_model,
                max_tokens=8000,
                system=[{"type": "text", "text": EXTRACTOR_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": render_transcript(session)}],
                output_format=LLMExtraction,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as e:
            log.warning("extractor LLM failed: %s", type(e).__name__)
            return None
        if response.stop_reason in ("refusal", "max_tokens"):
            log.info("extractor stopped: %s", response.stop_reason)
            return None
        return response.parsed_output

    async def update(self, session: CallSession) -> int:
        """Run extraction and merge validated findings into the session."""
        result = await self.extract(session)
        if result is None:
            return 0
        with session.lock:
            accepted = session.merge_findings(result.findings, source="llm")
            if result.stage != "none" and session.turns:
                last_scammer = max(
                    (t.index for t in session.turns if t.role == "scammer"), default=0
                )
                session.advance_stage(Stage(result.stage), last_scammer)
        return accepted
