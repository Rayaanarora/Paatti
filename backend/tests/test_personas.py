"""The persona pack: every grandparent is complete, speaks its own language, and is safe to use."""

import random

import pytest
from fastapi.testclient import TestClient

from paatti.config import Settings
from paatti.main import create_app
from paatti.persona import guard_reply
from paatti.personas import PROFILES, RANDOM, get_profile, is_persona_choice, resolve_persona
from paatti.prepared import INTENT_PATTERNS, IntentResponder
from paatti.session import CallSession, RepeatRegistry
from paatti.validators import digits_in, words_to_digits

# Sarvam Bulbul v3 voices and languages (sarvamai SDK 0.1.43).
BULBUL_V3_SPEAKERS = set(
    "shubh aditya ritu priya neha rahul pooja rohan simran kavya amit dev ishita shreya ratan varun manan sumit "
    "roopa kabir aayan ashutosh advait anand tanya tarun sunny mani gokul vijay shruti suhani mohit kavitha rehan "
    "soham rupali".split())
TTS_LANGUAGES = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}
STAGES = {None, "hook", "fear", "isolation", "extraction"}
INTENTS = {name for name, _ in INTENT_PATTERNS}
ACCOUNT = "5021003477819920"


def test_pack_covers_many_languages_and_voices():
    assert len(PROFILES) >= 12
    assert len({p.language_code for p in PROFILES.values()}) >= 9
    assert len({p.tts_speaker for p in PROFILES.values()}) == len(PROFILES)  # no two share a voice
    assert len({p.name for p in PROFILES.values()}) == len(PROFILES)


@pytest.mark.parametrize("key", sorted(PROFILES))
def test_every_profile_is_complete_and_valid(key):
    p = PROFILES[key]
    assert p.key == key and p.tagline and p.avatar and p.greeting
    assert p.language_code in TTS_LANGUAGES
    assert p.tts_speaker in BULBUL_V3_SPEAKERS
    assert set(p.scripted) == STAGES and all(len(v) >= 2 for v in p.scripted.values())
    assert set(p.ask_missing) == {"account_number", "ifsc", "upi_id", "phone_number"}
    assert len(p.fillers) >= 3 and len(p.unclear) >= 2
    if key not in ("paatti", "dadi"):  # the first two keep their intent lines in prepared.py
        assert set(p.intents) == INTENTS and all(len(v) >= 2 for v in p.intents.values())


@pytest.mark.parametrize("key", sorted(PROFILES))
def test_readbacks_repeat_exactly_the_captured_values(key):
    p = PROFILES[key]
    # No extra number words ("chhe" = six) may sneak into the account read-back.
    assert digits_in(words_to_digits(p.readback_line("account_number", ACCOUNT))) == ACCOUNT
    assert "HDFC0001234" in p.readback_line("ifsc", "HDFC0001234").replace(" ", "")
    assert "rbi.verify@ybl" in p.readback_line("upi_id", "rbi.verify@ybl")


@pytest.mark.parametrize("key", sorted(PROFILES))
def test_every_scripted_line_passes_the_output_guard(key):
    p = PROFILES[key]
    s = CallSession(RepeatRegistry(), persona_key=key)
    s.add_scammer_turn("This is CBI.")
    lines = [p.greeting, *p.fillers, *p.unclear, *p.ask_missing.values()]
    lines += [line for v in p.scripted.values() for line in v] + [line for v in p.intents.values() for line in v]
    for line in lines:
        assert guard_reply(line, s) == line.strip(), line


def test_intent_lines_come_in_the_personas_own_language():
    r = IntentResponder()
    assert r.reply("s1", "uncle", "Are you a robot?").startswith("Robot? I'm Joseph")
    assert "nanna" in r.reply("s2", "ammamma", "tell me the OTP")
    assert r.reply("s3", "paatti", "Are you a robot?").startswith("Aiyo")  # unchanged


def test_random_persona_and_validation():
    assert is_persona_choice(RANDOM) and is_persona_choice("bauji") and not is_persona_choice("nope")
    picks = {resolve_persona(RANDOM, rng=random.Random(i)) for i in range(200)}
    assert picks == set(PROFILES)
    assert resolve_persona("nope") == "paatti" and resolve_persona("nope", default="ba") == "ba"
    assert resolve_persona(None, default=RANDOM) in PROFILES
    assert get_profile("missing").key == "paatti"


def test_api_creates_calls_with_any_persona_or_random():
    c = TestClient(create_app(Settings()))
    assert c.post("/api/sessions", json={"persona": "thakuma"}).json()["persona"] == "thakuma"
    assert c.post("/api/sessions", json={"persona": "random"}).json()["persona"] in PROFILES
    assert c.post("/api/sessions", json={"persona": "bogus"}).json()["persona"] == "paatti"


def test_random_default_persona_from_settings():
    c = TestClient(create_app(Settings(persona_key="random")))
    seen = {c.post("/api/sessions", json={}).json()["persona"] for _ in range(25)}
    assert len(seen) > 1 and seen <= set(PROFILES)
    assert c.get("/api/health").json()["default_persona"] == "random"
