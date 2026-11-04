import pytest

from paatti.validators import (
    digits_in,
    is_valid_account,
    is_valid_ifsc,
    is_valid_phone,
    is_valid_upi,
    normalize_account,
    normalize_phone,
    readback_matches,
    words_to_digits,
)


@pytest.mark.parametrize("code", ["SBIN0001234", "HDFC0ABC123", "icic0000001"])
def test_valid_ifsc(code):
    assert is_valid_ifsc(code)


@pytest.mark.parametrize("code", ["SBIN1001234", "SBI00001234", "SBIN000123", "", "XXXX00000000"])
def test_invalid_ifsc(code):
    assert not is_valid_ifsc(code)


@pytest.mark.parametrize("upi", ["rbi.safe@ybl", "ramesh-12@okaxis", "9876543210@paytm"])
def test_valid_upi(upi):
    assert is_valid_upi(upi)


@pytest.mark.parametrize("upi", ["user@gmail.com", "a@b", "@ybl", "no-at-sign", "x@123"])
def test_invalid_upi(upi):
    assert not is_valid_upi(upi)


def test_account_normalisation_and_bounds():
    assert normalize_account("1234 5678-9012") == "123456789012"
    assert is_valid_account("123456789012")
    assert not is_valid_account("12345678")  # too short
    assert not is_valid_account("1" * 19)  # too long
    assert not is_valid_account("0000000000")  # all zeros


def test_phone_normalisation():
    assert normalize_phone("+91 98765 43210") == "9876543210"
    assert normalize_phone("09876543210") == "9876543210"
    assert is_valid_phone("9876543210")
    assert not is_valid_phone("1234567890")


def test_words_to_digits_handles_english_and_tamil_numbers():
    assert words_to_digits("one two three") == "1 2 3"
    assert words_to_digits("onnu rendu moonu") == "1 2 3"
    assert words_to_digits("double five") == "5 5"
    assert words_to_digits("triple zero") == "0 0 0"
    assert words_to_digits("note it nine") == "note it 9"


def test_digits_in():
    assert digits_in("1 2 3 - 4") == "1234"


def test_readback_matches():
    assert readback_matches("123456789012", "1-2-3-4-5-6-7-8-9-0-1-2")
    assert not readback_matches("123456789012", "1-2-3-4-5-6-7-8-9-0-1-3")
