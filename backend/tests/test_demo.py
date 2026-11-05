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
    sarvam = FakeSarvam()
    with TestClient(create_app(Settings(), sarvam=sarvam)):
        for _ in range(100):
            if len(sarvam.spoken) >= 40:
                break
            time.sleep(0.02)
    spoken = set(sarvam.spoken)
    for reply in [s.reply for s in STEPS["paatti"]]:
        assert all(chunk in spoken for chunk in split_for_tts(reply))
