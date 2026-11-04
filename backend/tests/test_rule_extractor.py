from paatti.rule_extractor import extract_from_turn
from paatti.schema import Stage


def _fields(findings):
    return {(f.field, f.value) for f in findings}


def test_extracts_agency_and_hook():
    r = extract_from_turn("This is CBI. Your Aadhaar is linked to a money-laundering case.")
    assert ("claimed_agency", "CBI") in _fields(r.findings)
    assert r.stage == Stage.HOOK


def test_extracts_amount_in_indian_format():
    r = extract_from_turn("You must pay Rs 4,50,000 as security deposit")
    assert ("amount", "450000") in _fields(r.findings)


def test_extracts_amount_in_lakh_words():
    r = extract_from_turn("transfer 4.5 lakh rupees today")
    assert ("amount", "450000") in _fields(r.findings)


def test_extracts_account_ifsc_and_upi():
    r = extract_from_turn(
        "Transfer to RBI safe account 3456 7890 1234, IFSC SBIN0004321 or UPI rbi.verify@ybl"
    )
    f = _fields(r.findings)
    assert ("account_number", "345678901234") in f
    assert ("ifsc", "SBIN0004321") in f
    assert ("upi_id", "rbi.verify@ybl") in f
    assert r.stage == Stage.EXTRACTION


def test_spoken_digits_become_account():
    r = extract_from_turn("account number is three four five six seven eight nine zero one two")
    assert ("account_number", "3456789012") in _fields(r.findings)


def test_ten_digit_number_near_phone_words_is_phone():
    r = extract_from_turn("Call me back on WhatsApp at +91 98765 43210")
    f = _fields(r.findings)
    assert ("phone_number", "9876543210") in f
    assert not any(field == "account_number" for field, _ in f)
    assert ("app_requested", "WhatsApp") in f


def test_isolation_stage():
    r = extract_from_turn("Do not tell anyone in your family. Stay on this Skype call.")
    assert r.stage == Stage.ISOLATION
    assert ("app_requested", "Skype") in _fields(r.findings)


def test_fear_stage():
    r = extract_from_turn("An arrest warrant is issued, police will come to your house")
    assert r.stage == Stage.FEAR


def test_quote_is_substring_of_turn():
    text = "Send it to UPI id cbi.case@okaxis immediately"
    r = extract_from_turn(text)
    upi = next(f for f in r.findings if f.field == "upi_id")
    assert upi.quote in text


def test_neutral_text_has_no_findings():
    r = extract_from_turn("Hello madam, good morning")
    assert r.findings == []
    assert r.stage is None


def test_agency_mention_without_self_identification_is_only_a_mention():
    r = extract_from_turn("Transfer to the RBI safe account now")
    assert not any(f.field == "claimed_agency" for f in r.findings)
    assert [m.value for m in r.agency_mentions] == ["RBI"]


def test_tamil_self_identification():
    r = extract_from_turn("Vanakkam amma, naan Enforcement Directorate officer pesuren")
    assert ("claimed_agency", "ED") in _fields(r.findings)
