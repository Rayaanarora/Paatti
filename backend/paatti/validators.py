"""Format checks for Indian banking identifiers and spoken-digit handling."""

from __future__ import annotations

import re

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
# NPCI handles are letters only (ybl, okaxis, paytm, ...). Email domains with a
# dot are rejected so we don't capture e-mail addresses as UPI IDs.
UPI_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.\-_]{1,255}@[a-zA-Z]{2,64}$")
PHONE_RE = re.compile(r"^[6-9]\d{9}$")

_DIGIT_WORDS = {
    # English
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Tamil (romanised)
    "poojyam": "0", "onnu": "1", "rendu": "2", "moonu": "3", "naalu": "4",
    "anju": "5", "aaru": "6", "ezhu": "7", "ettu": "8", "onbadhu": "9",
    # Hindi (romanised)
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "paanch": "5",
    "chhe": "6", "saat": "7", "aath": "8", "nau": "9",
}
_MULTIPLIERS = {"double": 2, "triple": 3}


def is_valid_ifsc(code: str) -> bool:
    return bool(IFSC_RE.match(code.strip().upper())) if code else False


def is_valid_upi(upi: str) -> bool:
    return bool(UPI_RE.match(upi.strip())) if upi else False


def normalize_account(raw: str) -> str:
    return re.sub(r"\D", "", raw)


def is_valid_account(acct: str) -> bool:
    return acct.isdigit() and 9 <= len(acct) <= 18 and set(acct) != {"0"}


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def is_valid_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(phone))


def words_to_digits(text: str) -> str:
    """Turn spoken digits ("double five", "onnu rendu") into numerals.

    Speech-to-text often returns digits as words; converting them lets the
    regex extractor see "3 4 5 6" as an account number.
    """
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower().strip(",.")
        if low in _MULTIPLIERS and i + 1 < len(tokens):
            nxt = tokens[i + 1].lower().strip(",.")
            if nxt in _DIGIT_WORDS:
                out.extend([_DIGIT_WORDS[nxt]] * _MULTIPLIERS[low])
                i += 2
                continue
        if low in _DIGIT_WORDS and _is_digit_context(tokens, i):
            out.append(_DIGIT_WORDS[low])
        else:
            out.append(tok)
        i += 1
    return " ".join(out)


# Words that are also ordinary English/Hindi words ("do it", "one day",
# "ek minute"); they only count as digits next to an unambiguous digit.
_AMBIGUOUS = {"do", "ek", "oh", "char", "one", "aaru", "nau"}


def _is_digit_context(tokens: list[str], i: int) -> bool:
    if tokens[i].lower().strip(",.") not in _AMBIGUOUS:
        return True

    def is_num(j: int) -> bool:
        if not 0 <= j < len(tokens):
            return False
        w = tokens[j].lower().strip(",.")
        return w.isdigit() or w in _MULTIPLIERS or (w in _DIGIT_WORDS and w not in _AMBIGUOUS)

    return is_num(i - 1) or is_num(i + 1)


def digits_in(text: str) -> str:
    return re.sub(r"\D", "", text)


def readback_matches(value: str, readback_text: str) -> bool:
    """True when the digits Paatti read back equal the captured digits."""
    want = digits_in(value)
    return bool(want) and digits_in(words_to_digits(readback_text)) == want
