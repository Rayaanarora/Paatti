import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from paatti.config import Settings
from paatti.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    return TestClient(create_app(Settings()))


def new_session(client):
    r = client.post("/api/sessions")
    assert r.status_code == 201
    return r.json()["session_id"]


def test_health_reports_offline_modes(client):
    h = client.get("/api/health").json()
    assert {k: h[k] for k in ("status", "persona", "extractor", "voice", "telephony")} == {
        "status": "ok", "persona": "scripted", "extractor": "rules", "voice": "browser", "telephony": False,
    }
    assert {p["key"] for p in h["personas"]} == {"paatti", "dadi"}


def test_text_turn_flow_and_readback(client):
    sid = new_session(client)
    r = client.post(f"/api/sessions/{sid}/turn", json={"text": "This is CBI, your Aadhaar is in a case"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"]["stage"] == "hook"
    assert body["state"]["intel"]["claimed_agency"]["value"] == "CBI"

    r = client.post(f"/api/sessions/{sid}/turn", json={"text": "Transfer to account 3456 7890 1234 immediately"})
    reply = r.json()["paatti"]["text"]
    assert "3-4-5-6-7-8-9-0-1-2-3-4" in reply  # Paatti reads it back

    r = client.post(f"/api/sessions/{sid}/turn", json={"text": "Yes correct, do it fast"})
    assert r.json()["state"]["intel"]["account_number"]["confirmed"] is True


def test_turn_validation(client):
    sid = new_session(client)
    assert client.post(f"/api/sessions/{sid}/turn", json={"text": ""}).status_code == 422
    assert client.post(f"/api/sessions/{sid}/turn", json={"text": "x" * 2001}).status_code == 422
    assert client.post("/api/sessions/nope/turn", json={"text": "hi"}).status_code == 404


def test_ended_call_rejects_turns(client):
    sid = new_session(client)
    client.post(f"/api/sessions/{sid}/end")
    assert client.post(f"/api/sessions/{sid}/turn", json={"text": "hello"}).status_code == 409


def test_reports(client):
    sid = new_session(client)
    for text in [
        "This is Mumbai Police cyber cell. A parcel in your name has drugs.",
        "You must pay Rs 4,50,000 to the RBI safe account 3456 7890 1234 IFSC SBIN0004321",
        "Yes correct",
    ]:
        client.post(f"/api/sessions/{sid}/turn", json={"text": text})
    rep = client.get(f"/api/sessions/{sid}/report.json").json()
    assert rep["amount_demanded"]["value"] == "450000"
    assert rep["ncrp_fields"]["suspect_bank_account"] == "345678901234"
    assert rep["ncrp_fields"]["suspect_ifsc"] is None  # IFSC not yet read back
    assert "ifsc" in rep["unconfirmed_identifiers"]
    assert {s["code"] for s in rep["legal_sections"]} >= {"BNS 318", "BNS 319"}
    assert len(rep["chain_of_custody"]["transcript_sha256"]) == 64

    pdf = client.get(f"/api/sessions/{sid}/report.pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_websocket_call(client):
    sid = new_session(client)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "audio_chunk", "data": base64.b64encode(b"RIFFdata").decode()})
        ws.send_json({"type": "scammer_text", "text": "Send to UPI cbi.case@okaxis now"})
        msgs = [ws.receive_json() for _ in range(3)]
        assert [m["type"] for m in msgs] == ["turn", "state", "turn"]
        assert msgs[1]["state"]["intel"]["upi_id"]["value"] == "cbi.case@okaxis"
        assert "cbi.case@okaxis" in msgs[2]["turn"]["text"]  # read-back
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "audio_utterance", "data": base64.b64encode(b"x").decode()})
        assert "not configured" in ws.receive_json()["message"]
        ws.send_json({"type": "end"})
        assert ws.receive_json()["state"]["ended"] is True
    rep = client.get(f"/api/sessions/{sid}/report.json").json()
    assert rep["chain_of_custody"]["audio_sha256"] == hashlib.sha256(b"RIFFdata").hexdigest()


def test_websocket_unknown_session(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/sessions/missing") as ws:
            ws.receive_json()


def test_session_rate_limit(monkeypatch):
    monkeypatch.setenv("PAATTI_SESSIONS_PER_MIN", "2")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = TestClient(create_app(Settings()))
    assert c.post("/api/sessions").status_code == 201
    assert c.post("/api/sessions").status_code == 201
    assert c.post("/api/sessions").status_code == 429


def test_dispatch_history_and_report_safeguards(client):
    sid = new_session(client)
    for text in ["This is CBI. Pay Rs 50,000 to account 3456 7890 1234", "yes correct"]:
        client.post(f"/api/sessions/{sid}/turn", json={"text": text})
    assert client.post(f"/api/sessions/{sid}/dispatch", json={"destination": "nowhere"}).status_code == 422
    d = client.post(f"/api/sessions/{sid}/dispatch", json={"destination": "ncrp_1930"}).json()
    assert d["reference"].startswith("NCRP-") and d["within_golden_hour"] and d["mock"]
    assert d["identifiers_sent"]["suspect_bank_account"] == "345678901234"
    rep = client.get(f"/api/sessions/{sid}/report.json").json()
    assert rep["status"].startswith("LEAD")
    assert "innocent" in rep["safeguards"]["framing_risk"]
    assert rep["corroboration"][0]["readback_confirmed"] is True
    assert rep["dispatches"][0]["reference"] == d["reference"]
    assert client.get(f"/api/sessions/{sid}/report.pdf").content.startswith(b"%PDF")
    hist = client.get("/api/history").json()
    assert hist["counts"]["dispatches"] == 1 and hist["mule_registry"][0]["value"] == "345678901234"
    listed = client.get("/api/sessions").json()
    assert listed[0]["session_id"] == sid and "intel" not in listed[0]


def test_second_screen_watches_live_call(client):
    sid = new_session(client)
    client.post(f"/api/sessions/{sid}/turn", json={"text": "This is CBI"})
    with client.websocket_connect(f"/ws/sessions/{sid}") as watcher, \
            client.websocket_connect(f"/ws/sessions/{sid}") as driver:
        first = [watcher.receive_json() for _ in range(3)]  # state + 2 history turns
        assert first[0]["type"] == "state" and [m["type"] for m in first[1:]] == ["turn", "turn"]
        [driver.receive_json() for _ in range(3)]
        driver.send_json({"type": "scammer_text", "text": "Arrest warrant issued"})
        got = [watcher.receive_json() for _ in range(3)]
        assert [m["type"] for m in got] == ["turn", "state", "turn"]
        assert got[1]["state"]["stage"] == "fear"


def test_create_session_with_persona(client):
    r = client.post("/api/sessions", json={"channel": "web-phone", "persona": "dadi"})
    assert r.json()["persona"] == "dadi" and r.json()["channel"] == "web-phone"
    assert client.post("/api/sessions", json={"channel": "phone"}).status_code == 422


def test_cors_allows_vercel_dashboard_origins(monkeypatch):
    monkeypatch.setenv("PAATTI_CORS_ORIGIN_REGEX", r"https://paatti[a-z0-9-]*\.vercel\.app")
    c = TestClient(create_app(Settings()))
    ok = c.options("/api/sessions", headers={"Origin": "https://paatti-console-abc123.vercel.app",
                                              "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://paatti-console-abc123.vercel.app"
    bad = c.options("/api/sessions", headers={"Origin": "https://evil.example",
                                               "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
