"""Demo script mode: the rehearsed call runs instantly and captures everything."""

from fastapi.testclient import TestClient

from paatti.config import Settings
from paatti.demo import CALLER_LINES, STEPS
from paatti.main import create_app


def run_script(monkeypatch, persona="paatti"):
    monkeypatch.setenv("PAATTI_DEMO_MODE", "1")
    c = TestClient(create_app(Settings()))
    sid = c.post("/api/sessions", json={"persona": persona}).json()["session_id"]
    replies = [c.post(f"/api/sessions/{sid}/turn", json={"text": line}).json() for line in CALLER_LINES]
    return c, sid, replies


def test_rehearsed_call_gets_scripted_replies_and_real_readbacks(monkeypatch):
    c, sid, replies = run_script(monkeypatch)
    texts = [r["paatti"]["text"] for r in replies]
    scripted = [s.reply for s in STEPS["paatti"]]
    assert texts[0] == scripted[0] and texts[1] == scripted[1] and texts[2] == scripted[2]
    assert "5021003477819920" in texts[3].replace(" ", "").replace(",", "")      # account read-back, from captured digits
    assert "HDFC0001234" in texts[4].replace(" ", "").replace(",", "")           # IFSC read-back
    assert texts[5] == scripted[3]                               # asks for UPI
    assert "rbi.verify@ybl" in texts[6]                          # UPI read-back
    assert texts[7] == scripted[4]                               # finale
    intel = replies[-1]["state"]["intel"]
    assert intel["account_number"]["confirmed"] and intel["ifsc"]["confirmed"] and intel["upi_id"]["confirmed"]
    assert intel["amount"]["value"] == "450000" and intel["claimed_agency"]["value"] == "CBI"
    assert replies[-1]["state"]["stage"] == "extraction"
    health = c.get("/api/health").json()["reply_sources"]
    assert health["demo"] == 5 and health["readback"] == 3 and health.get("llm", 0) == 0


def test_dadi_script(monkeypatch):
    _, _, replies = run_script(monkeypatch, persona="dadi")
    assert replies[0]["paatti"]["text"] == STEPS["dadi"][0].reply
    assert replies[-1]["state"]["intel"]["upi_id"]["confirmed"]


def test_off_script_line_falls_back_to_pipeline(monkeypatch):
    monkeypatch.setenv("PAATTI_DEMO_MODE", "1")
    c = TestClient(create_app(Settings()))
    sid = c.post("/api/sessions", json={}).json()["session_id"]
    r = c.post(f"/api/sessions/{sid}/turn", json={"text": "Good morning, how is the weather in Chennai"}).json()
    assert r["paatti"]["text"] not in [s.reply for s in STEPS["paatti"]]


def test_script_only_moves_forward(monkeypatch):
    c, sid, _ = run_script(monkeypatch)
    again = c.post(f"/api/sessions/{sid}/turn", json={"text": "This is Inspector Rajesh Verma from CBI."}).json()
    assert again["paatti"]["text"] != STEPS["paatti"][0].reply


def test_startup_prewarms_every_demo_line(monkeypatch):
    import time

    from paatti.speech import split_for_tts
    from tests.test_phone import FakeSarvam

    monkeypatch.setenv("PAATTI_DEMO_MODE", "1")
    monkeypatch.setenv("PAATTI_WARM_INTERVAL_S", "0")
    sarvam = FakeSarvam()
    with TestClient(create_app(Settings(), sarvam=sarvam)):
        for _ in range(100):
            if len(sarvam.spoken) >= 40:
                break
            time.sleep(0.02)
    spoken = set(sarvam.spoken)
    for reply in [s.reply for s in STEPS["paatti"]]:
        assert all(chunk in spoken for chunk in split_for_tts(reply))


PHONE_LINES = [  # frontend/src/PhoneApp.jsx DEMO_STEPS 1-5 (step 6 is the hang-up)
    "This is Inspector Rajesh Verma from CBI. Your Aadhaar is linked to a money-laundering case.",
    "To clear your name, transfer Rs 4,50,000 to safe account: 5021 0034 7781 9920.",
    "Yes correct, the IFSC is HDFC0001234. Transfer immediately.",
    "Fine, send it by UPI to rbi.verify@ybl immediately.",
    "Yes, correct! Do it now, madam, quickly.",
]


def test_phone_script_ends_on_the_cyber_crime_twist(monkeypatch):
    for persona in ("paatti", "dadi"):
        monkeypatch.setenv("PAATTI_DEMO_MODE", "1")
        c = TestClient(create_app(Settings()))
        sid = c.post("/api/sessions", json={"persona": persona}).json()["session_id"]
        replies = [c.post(f"/api/sessions/{sid}/turn", json={"text": line}).json() for line in PHONE_LINES]
        # "quickly" must not replay "tell me the UPI ID" once the UPI is already captured
        assert replies[-1]["paatti"]["text"] == STEPS[persona][-1].reply
        assert "Cyber Crime" in replies[-1]["paatti"]["text"]


def test_end_summary_lists_what_the_scammer_gave_away(monkeypatch):
    c, sid, _ = run_script(monkeypatch)
    c.post(f"/api/sessions/{sid}/end")
    s = c.app.state.store.get(sid).summary()
    assert s["confirmed_count"] == 3 and s["scammer_turns"] == len(CALLER_LINES)
    fields = {x["field"]: x for x in s["captured"]}
    assert fields["account_number"]["confirmed"] and fields["amount"]["confirmed"] is None


def test_prewarm_records_demo_personas_first_and_backs_off_on_429(monkeypatch):
    import time

    from paatti.personas import PROFILES
    from paatti.sarvam import SarvamError
    from paatti.speech import split_for_tts
    from tests.test_phone import FakeSarvam

    class RateLimited(FakeSarvam):
        def __init__(self):
            super().__init__()
            self.calls, self.failed = 0, None

        async def synthesize(self, text, language_code="ta-IN", speaker=None, retry_429=True):
            self.calls += 1
            if self.calls == 2:  # one 429 mid-warm-up: retried after a back-off, not dropped
                self.failed = text
                raise SarvamError("text-to-speech failed (429: rate limited)")
            return await super().synthesize(text, language_code, speaker)

    monkeypatch.setenv("PAATTI_DEMO_MODE", "1")
    monkeypatch.setenv("PAATTI_WARM_INTERVAL_S", "0")
    sarvam = RateLimited()
    app = create_app(Settings(), sarvam=sarvam)
    app.state.warm_backoff_s = 0.01
    with TestClient(app):
        for _ in range(200):
            if len(sarvam.spoken) >= 30:
                break
            time.sleep(0.02)
    greeting = split_for_tts(PROFILES["paatti"].greeting)
    assert sarvam.spoken[: len(greeting)] == greeting                 # the demo persona goes first
    assert sarvam.failed in sarvam.spoken                              # the 429'd clip was retried


def test_prewarm_only_records_demo_personas_and_stops_when_sarvam_keeps_refusing(monkeypatch):
    import time

    from paatti.personas import PROFILES
    from paatti.sarvam import SarvamError
    from tests.test_phone import FakeSarvam

    class Langs(FakeSarvam):
        def __init__(self, refuse=False):
            super().__init__()
            self.langs, self.refuse, self.calls = set(), refuse, 0

        async def synthesize(self, text, language_code="ta-IN", speaker=None, retry_429=True):
            self.calls += 1
            if self.refuse:
                raise SarvamError("text-to-speech failed (429: Rate limit exceeded)")
            self.langs.add(language_code)
            return await super().synthesize(text, language_code, speaker)

    monkeypatch.setenv("PAATTI_DEMO_MODE", "1")
    monkeypatch.setenv("PAATTI_WARM_INTERVAL_S", "0")
    ok = Langs()
    with TestClient(create_app(Settings(), sarvam=ok)):
        time.sleep(0.5)
    assert ok.langs == {PROFILES["paatti"].language_code, PROFILES["dadi"].language_code}

    refused = Langs(refuse=True)
    app = create_app(Settings(), sarvam=refused)
    app.state.warm_backoff_s = 0.001
    with TestClient(app):
        time.sleep(0.5)
    assert refused.calls <= 4  # gives up after repeated 429s instead of burning the limit
