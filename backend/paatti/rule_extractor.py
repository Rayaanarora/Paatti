"""Deterministic, regex-based extractor.

Runs on every scammer turn with no network dependency, so the dashboard keeps
working when the LLM is slow or unavailable. The LLM extractor adds recall on
top; both pass through the same validators.
"""

from __future__ import annotations

import re

from .schema import Finding, Stage, TurnExtraction
from .validators import (
    is_valid_account,
    is_valid_ifsc,
    is_valid_phone,
    is_valid_upi,
    normalize_account,
    normalize_phone,
    words_to_digits,
)

AGENCIES = {
    "CBI": [r"\bCBI\b", r"central bureau of investigation"],
    "ED": [r"\bED\b", r"enforcement directorate"],
    "RBI": [r"\bRBI\b", r"reserve bank"],
    "NCB": [r"\bNCB\b", r"narcotics control bureau"],
    "Mumbai Police": [r"mumbai police"],
    "Cyber Crime Police": [r"cyber ?crime"],
    "Customs": [r"\bcustoms\b"],
    "TRAI": [r"\bTRAI\b", r"telecom regulatory"],
    "Income Tax Department": [r"income ?tax"],
    "Police": [r"\bpolice\b"],
    "Courier (FedEx/DHL)": [r"\bfedex\b", r"\bdhl\b"],
}

# Phrases where the caller claims to be (or speak for) the agency.
SELF_ID = re.compile(
    r"\b(this is|i am|i'm|calling from|speaking from|from|naan|naa|main|hum|officer|inspector|"
    r"department|pesuren|bol raha|here)\b",
    re.IGNORECASE,
)

APPS = {
    "Skype": r"\bskype\b",
    "WhatsApp": r"\bwhats ?app\b",
    "AnyDesk": r"\bany ?desk\b",
    "TeamViewer": r"\bteam ?viewer\b",
    "Zoom": r"\bzoom\b",
    "Telegram": r"\btelegram\b",
    "Google Meet": r"\bgoogle meet\b",
}

STAGE_PATTERNS: dict[Stage, list[str]] = {
    Stage.HOOK: [
        r"\bcase\b", r"aadhaar", r"\bparcel\b", r"\bpackage\b", r"money[- ]laundering",
        r"\bsim\b.*\bblock", r"illegal", r"\bdrugs?\b", r"complaint",
    ],
    Stage.FEAR: [
        r"\barrest", r"\bwarrant\b", r"\bjail\b", r"\bprison\b", r"legal action",
        r"police will", r"\bfir\b", r"digital arrest", r"freez",
    ],
    Stage.ISOLATION: [
        r"don'?t tell", r"do not tell", r"not tell anyone", r"stay on (this|the)",
        r"don'?t (disconnect|cut)", r"do not (disconnect|cut)", r"confidential",
        r"\balone\b", r"secret", r"camera on", r"video call",
    ],
    Stage.EXTRACTION: [
        r"\btransfer\b", r"\bpay\b", r"\bsend (it|money|the)", r"safe account",
        r"verification (amount|account)", r"\bdeposit\b", r"\bifsc\b", r"\bupi\b",
        r"account (number|no)", r"\bgpay\b|\bphonepe\b|\bpaytm\b",
    ],
}

IFSC_FIND = re.compile(r"\b([A-Za-z]{4}0[A-Za-z0-9]{6})\b")
# A trailing sentence full stop is fine ("pay rbi.verify@ybl."); a dot followed by more
# letters means an e-mail domain ("a@gmail.com"), which is not a UPI ID.
UPI_FIND = re.compile(r"(?<![\w.@])([a-zA-Z0-9][a-zA-Z0-9.\-_]{1,255}@[a-zA-Z]{2,64})\b(?!\.[a-zA-Z])")
# Runs of digits, optionally separated by single spaces or hyphens.
DIGIT_RUN = re.compile(r"(?:\+?\d[\d \-]{7,30}\d)")
AMOUNT_NUM = re.compile(
    r"(?:rs\.?|inr|₹|rupees?)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|thousand|k)?"
    r"|([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|thousand)\b"
    r"|([\d,]+(?:\.\d+)?)\s*(?:rs\.?|rupees?|inr)\b",
    re.IGNORECASE,
)
PHONE_CONTEXT = re.compile(
    r"call|phone|mobile|number to|whats ?app|contact|ring|dial|sms|text me", re.IGNORECASE
)
ACCOUNT_CONTEXT = re.compile(r"account|a/c|acct|khata|bank", re.IGNORECASE)

_MULT = {"lakh": 100_000, "lac": 100_000, "crore": 10_000_000, "thousand": 1000, "k": 1000}


def _quote(text: str, start: int, end: int, pad: int = 40) -> str:
    """Return the sentence-ish window around a match, verbatim from the turn."""
    s = max(0, start - pad)
    e = min(len(text), end + pad)
    # Expand to word boundaries so quotes don't cut words in half.
    while s > 0 and not text[s - 1].isspace():
        s -= 1
    while e < len(text) and not text[e].isspace():
        e += 1
    return text[s:e].strip()


def _amount_value(num: str, unit: str | None) -> str | None:
    try:
        n = float(num.replace(",", ""))
    except ValueError:
        return None
    if unit:
        key = unit.lower().rstrip("s")
        n *= _MULT.get(key, 1)
    if n <= 0:
        return None
    return str(int(round(n)))


def detect_stage(text: str) -> Stage | None:
    """Highest stage whose cue words appear in the text."""
    found = None
    for stage, pats in STAGE_PATTERNS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in pats):
            found = stage  # dict order is stage order, so later wins
    return found


def extract_from_turn(text: str, turn_index: int = 0) -> TurnExtraction:
    findings: list[Finding] = []

    def add(field: str, value: str, start: int, end: int, source_text: str = text) -> None:
        quote = _quote(source_text, start, end)
        if source_text is not text and quote not in text:
            quote = text.strip()[:500]
        findings.append(Finding(field=field, value=value, quote=quote[:500], turn_index=turn_index))

    mentions: list[Finding] = []
    agency = _find_agency(text)
    if agency:
        name, start, end = agency
        add("claimed_agency", name, start, end)
        if not SELF_ID.search(text[max(0, start - 60): end + 25]):
            mentions.append(findings.pop())

    for name, p in APPS.items():
        m = re.search(p, text, re.IGNORECASE)
        if m:
            add("app_requested", name, m.start(), m.end())

    for m in IFSC_FIND.finditer(text):
        code = m.group(1).upper()
        if is_valid_ifsc(code):
            add("ifsc", code, m.start(), m.end())

    upi_spans: list[tuple[int, int]] = []
    for m in UPI_FIND.finditer(text):
        if is_valid_upi(m.group(1)):
            upi_spans.append(m.span())
            add("upi_id", m.group(1), m.start(), m.end())

    amount_spans: list[tuple[int, int]] = []
    for m in AMOUNT_NUM.finditer(text):
        num = m.group(1) or m.group(3) or m.group(5)
        unit = m.group(2) or m.group(4)
        val = _amount_value(num, unit) if num else None
        if val:
            amount_spans.append(m.span())
            add("amount", val, m.start(), m.end())
            break

    # Numbers: work on a copy where spoken digits are numerals.
    spoken = words_to_digits(text)
    source = spoken if spoken != text else text
    for m in DIGIT_RUN.finditer(source):
        if source is text and any(s <= m.start() < e for s, e in upi_spans + amount_spans):
            continue
        raw = m.group(0)
        digits = normalize_account(raw)
        window = source[max(0, m.start() - 40): m.end() + 20]
        phone = normalize_phone(raw)
        looks_phone = is_valid_phone(phone) and (
            raw.strip().startswith("+") or len(digits) in (11, 12) or PHONE_CONTEXT.search(window)
        )
        if looks_phone and not (ACCOUNT_CONTEXT.search(window) and not PHONE_CONTEXT.search(window)):
            add("phone_number", phone, m.start(), m.end(), source)
        elif is_valid_account(digits):
            add("account_number", digits, m.start(), m.end(), source)

    return TurnExtraction(findings=findings, agency_mentions=mentions, stage=detect_stage(text))


def _find_agency(text: str) -> tuple[str, int, int] | None:
    """Earliest agency named in the turn; at the same position, the more specific name."""
    best: tuple[int, int, str, int] | None = None  # (start, priority, name, end)
    for priority, (name, pats) in enumerate(AGENCIES.items()):
        for p in pats:
            m = re.search(p, text, re.IGNORECASE)
            if m and (best is None or (m.start(), priority) < best[:2]):
                best = (m.start(), priority, name, m.end())
    return (best[2], best[0], best[3]) if best else None
