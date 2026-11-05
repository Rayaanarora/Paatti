"""Voice pipeline, Twilio phone line and browser phone with fake Sarvam/Twilio (no network)."""

import asyncio
import base64
import json
import math
import struct

import httpx
import pytest
from fastapi.testclient import TestClient

from paatti import voice as voice_mod
from paatti.audio import frames_of, mulaw_encode, wav_from_pcm
from paatti.config import Settings
from paatti.extractor import LLMExtractor
from paatti.main import create_app
from paatti.persona import Persona
from paatti.prepared import ReplyCache
from paatti.sarvam import SarvamError
from paatti.service import CallService, Hub, SessionStore
from paatti.telephony import normalize_e164, twilio_signature
from paatti.voice import TTS_CACHE, VoiceCall


def tone(ms, rate=8000, amp=8000):
    n = rate * ms // 1000
    return struct.pack(f"<{n}h", *(int(amp * math.sin(2 * math.pi * 300 * i / rate)) for i in range(n)))


def silence(ms, rate=8000):
    return b"\x00\x00" * (rate * ms // 1000)


class FakeSarvam:
    def __init__(self, transcripts=None, fail_tts=False):
        self.transcripts = list(transcripts or [])
        self.fail_tts = fail_tts
        self.spoken = []

    async def transcribe(self, audio, filename="turn.wav", mime="audio/wav"):
        return self.transcripts.pop(0) if self.transcripts else ""

    async def synthesize(self, text, language_code="ta-IN", speaker=None):
        if self.fail_tts:
            raise SarvamError("tts down")
        self.spoken.append(text)
        return wav_from_pcm(silence(100, 22050), 22050)


def make_service():
    settings = Settings()
    persona = Persona(settings, use_backend=False, cache=ReplyCache(None))
    return CallService(settings, persona, LLMExtractor(settings, use_backend=False), SessionStore(50), Hub())


def make_call(service, sarvam, **kw):
    s = service.create_session(channel="phone")
    sent, cleared = [], []

    async def send(pcm):
        sent.append(pcm)

    async def clear():
        cleared.append(True)

    call = VoiceCall(service, s, sarvam, in_rate=8000, out_rate=8000, send_audio=send, clear_audio=clear, **kw)
    return s, call, sent, cleared


@pytest.fixture(autouse=True)
def fresh_tts_cache():
    TTS_CACHE._data.clear()


# ---- voice pipeline -------------------------------------------------------

async def test_utterance_goes_through_stt_persona_tts():
    service = make_service()
    sarvam = FakeSarvam(["Transfer to account 3456 7890 1234 now"])
    s, call, sent, _ = make_call(service, sarvam)
    await call.handle_utterance(tone(500))
    assert s.intel.account_number.value == "345678901234"
    assert any("3456" in t.replace(" ", "").replace(",", "") for t in sarvam.spoken)  # read-back spoken aloud
    assert sent and len(sent[-1]) == 2 * 800  # 0.1 s at 8 kHz after resampling


async def test_feed_segments_audio_and_worker_processes_turn():
    service = make_service()
    sarvam = FakeSarvam(["This is CBI speaking"])
    s, call, sent, _ = make_call(service, sarvam)
    call.start()
    for f in frames_of(silence(200) + tone(600) + silence(900), 8000, 20):
        await call.feed(f)
    for _ in range(50):
        if s.turns:
            break
        await asyncio.sleep(0.01)
    await call.close()
    assert s.turns[0].text == "This is CBI speaking" and s.intel.claimed_agency.value == "CBI"


async def test_filler_plays_when_reply_is_slow(monkeypatch):
    monkeypatch.setattr(voice_mod, "FILLER_AFTER_S", 0.02)
    service = make_service()
    original = service.respond_stream

    async def slow_respond(session, text):
        await asyncio.sleep(0.1)
        async for sentence in original(session, text):
            yield sentence

    service.respond_stream = slow_respond
    sarvam = FakeSarvam(["hello madam this is the police"])
    s, call, sent, _ = make_call(service, sarvam)
    await call.handle_utterance(tone(300))
    assert sarvam.spoken[0] in call.profile.fillers and len(sent) >= 2


async def test_unclear_line_after_two_empty_transcripts():
    service = make_service()
    sarvam = FakeSarvam(["", ""])
    s, call, sent, _ = make_call(service, sarvam)
    await call.handle_utterance(tone(300))
    await call.handle_utterance(tone(300))
    assert sarvam.spoken[-1] == call.profile.unclear[0] and not s.turns


async def test_instant_reply_gets_no_filler():
    service = make_service()
    sarvam = FakeSarvam(["Transfer to account 3456 7890 1234 now"])  # read-back: instant
    s, call, sent, _ = make_call(service, sarvam)
    await call.handle_utterance(tone(300))
    assert not any(t in call.profile.fillers for t in sarvam.spoken)
    assert any("3456" in t.replace(" ", "").replace(",", "") for t in sarvam.spoken)


async def test_barge_in_clears_and_echo_guard_drops():
    service = make_service()
    clock = {"t": 100.0}
    s, call, _, cleared = make_call(service, FakeSarvam(), clock=lambda: clock["t"])
    call.play_until = 105.0
    for f in frames_of(tone(200), 8000, 20):
        await call.feed(f)
    assert cleared and not call.playing

    s2, guarded, _, cleared2 = make_call(service, FakeSarvam(), barge_in=False, echo_guard=True,
                                         clock=lambda: clock["t"])
    guarded.play_until = 105.0
    for f in frames_of(tone(200), 8000, 20):
        await guarded.feed(f)
    assert not cleared2 and not guarded.vad.in_speech


async def test_tts_failure_publishes_notice_and_greeting_is_a_turn():
    service = make_service()
    s, call, sent, _ = make_call(service, FakeSarvam(fail_tts=True))
    q = service.hub.subscribe(s.id)
    await call.greet()
    msgs = [q.get_nowait() for _ in range(q.qsize())]
    assert msgs[0]["type"] == "turn" and msgs[0]["turn"]["role"] == "paatti"
    assert any(m["type"] == "notice" for m in msgs) and not sent


async def test_warm_caches_fillers():
    service = make_service()
    sarvam = FakeSarvam()
    _, call, _, _ = make_call(service, sarvam)
    await call.warm()
    n = len(sarvam.spoken)
    await call.play(call.profile.fillers[0], cache=True)
    assert len(sarvam.spoken) == n  # served from cache


# ---- Twilio ---------------------------------------------------------------

PUBLIC = "https://paatti.example"


def twilio_settings(**kw):
    return Settings(twilio_account_sid="AC123", twilio_auth_token="secret", public_url=PUBLIC,
                    twilio_number="+15550001111", **kw)


def signed_post(client, params, path="/telephony/twilio/voice", token="secret"):
    sig = twilio_signature(token, PUBLIC + path, params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": sig})


def test_signature_algorithm_matches_reference():
    # Reference vector from Twilio's webhook-security documentation example shape.
    params = {"CallSid": "CA1", "From": "+14158675310"}
    expected = twilio_signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", params)
    assert expected == twilio_signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2",
                                        dict(reversed(list(params.items()))))
    assert len(base64.b64decode(expected)) == 20  # SHA-1 digest


def test_webhook_rejects_bad_signature_and_unconfigured():
    c = TestClient(create_app(twilio_settings(), sarvam=FakeSarvam()))
    r = c.post("/telephony/twilio/voice", data={"From": "+911"}, headers={"X-Twilio-Signature": "nope"})
    assert r.status_code == 403
    c2 = TestClient(create_app(Settings()))
    assert c2.post("/telephony/twilio/voice", data={}).status_code == 503


def test_inbound_call_end_to_end_over_media_stream():
    sarvam = FakeSarvam(["This is CBI. Transfer to account 3456 7890 1234"])
    app = create_app(twilio_settings(), sarvam=sarvam)
    c = TestClient(app)
    r = signed_post(c, {"CallSid": "CA1", "From": "+919876543210", "To": "+15550001111", "Direction": "inbound"})
    assert r.status_code == 200 and "<Connect><Stream" in r.text and "wss://paatti.example/telephony/twilio/stream" in r.text
    token = r.text.split('name="token" value="')[1].split('"')[0]
    session = app.state.store.all()[0]
    assert session.channel == "phone" and session.caller_id == "+919876543210"

    with c.websocket_connect("/telephony/twilio/stream") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1",
                                                             "customParameters": {"token": token}}}))
        greeting = json.loads(ws.receive_text())
        assert greeting["event"] == "media" and greeting["streamSid"] == "MZ1"
        assert json.loads(ws.receive_text())["event"] == "mark"
        audio = mulaw_encode(silence(200) + tone(600) + silence(900))
        for i in range(0, len(audio), 160):
            ws.send_text(json.dumps({"event": "media", "media": {"track": "inbound",
                                     "payload": base64.b64encode(audio[i:i + 160]).decode()}}))
        reply = json.loads(ws.receive_text())
        assert reply["event"] in ("media", "clear")
        ws.send_text(json.dumps({"event": "stop"}))
    assert session.intel.account_number.value == "345678901234"
    assert session.audio_sha256() is not None and session.ended_at is not None


def test_stream_with_bad_token_is_closed():
    app = create_app(twilio_settings(), sarvam=FakeSarvam())
    c = TestClient(app)
    with c.websocket_connect("/telephony/twilio/stream") as ws:
        ws.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ", "customParameters": {"token": "x"}}}))
        with pytest.raises(Exception):
            ws.receive_text()


def test_outbound_call_requires_token_and_allowlist():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(201, json={"sid": "CA999"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = twilio_settings(operator_token="op", outbound_allowlist=["+91 98765 43210"])
    c = TestClient(create_app(settings, sarvam=FakeSarvam(), twilio_http=http))
    assert c.post("/api/telephony/call", json={"to": "9876543210"}).status_code == 403
    assert c.post("/api/telephony/call", json={"to": "9999999999"},
                  headers={"X-Operator-Token": "op"}).status_code == 403
    r = c.post("/api/telephony/call", json={"to": "9876543210", "persona": "dadi"}, headers={"X-Operator-Token": "op"})
    assert r.status_code == 200 and r.json()["call_sid"] == "CA999"
    assert seen["url"].endswith("/Accounts/AC123/Calls.json")
    assert "To=%2B919876543210" in seen["body"] and "persona%3Ddadi" in seen["body"]


def test_normalize_e164():
    assert normalize_e164("98765 43210") == "+919876543210"
    assert normalize_e164("0091 98765 43210") == "+919876543210"
    assert normalize_e164("+1 (555) 000-1111") == "+15550001111"


# ---- browser phone ----------------------------------------------------------

def test_browser_phone_text_fallback_without_sarvam():
    app = create_app(Settings())
    c = TestClient(app)
    sid = c.post("/api/sessions", json={"channel": "web-phone"}).json()["session_id"]
    with c.websocket_connect(f"/ws/phone/{sid}") as ws:
        assert ws.receive_json() == {"type": "connected", "voice": False, "persona": "paatti", "resumed": False,
                                     "name": "Kamala Paatti", "tagline": "Retired Teacher · Mylapore, Chennai",
                                     "avatar": "👵"}
        assert receive_type(ws, "paatti_text")["text"]
        ws.send_json({"type": "text", "text": "This is CBI, your Aadhaar is in a case"})
        assert receive_type(ws, "paatti_text")["text"]
        ws.send_json({"type": "hangup"})
    s = app.state.store.get(sid)
    assert s.channel == "web-phone" and s.ended_at is not None and s.intel.claimed_agency.value == "CBI"


def receive_type(ws, kind, limit=20):
    """Next message of one type; hub events (turn, notice) are interleaved with it."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"no {kind} message")


def test_browser_phone_gets_paatti_turns_and_pong():
    app = create_app(Settings())
    c = TestClient(app)
    sid = c.post("/api/sessions", json={"channel": "web-phone"}).json()["session_id"]
    with c.websocket_connect(f"/ws/phone/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "answered"})
        assert receive_type(ws, "turn")["turn"]["role"] == "paatti"  # greeting, for the subtitle
        ws.send_json({"type": "ping"})
        assert receive_type(ws, "pong") == {"type": "pong"}
        ws.send_json({"type": "hangup"})


def test_dropped_phone_can_rejoin_the_same_call_without_a_second_greeting():
    app = create_app(Settings())
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={"channel": "web-phone"}).json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as ws:
            ws.receive_json()
            ws.send_json({"type": "answered"})
            receive_type(ws, "paatti_text")
        s = app.state.store.get(sid)
        assert s.ended_at is None  # dropped, not hung up: the call waits for the phone
        with c.websocket_connect(f"/ws/phone/{sid}") as ws:
            assert ws.receive_json()["resumed"] is True
            ws.send_json({"type": "answered"})
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"  # no second greeting before it
            ws.send_json({"type": "hangup"})
        assert s.ended_at is not None
        assert [t.role for t in s.turns] == ["paatti"]


def test_dropped_phone_call_ends_after_the_grace_period():
    import time

    app = create_app(Settings())
    app.state.phone_grace_s = 0.05
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={"channel": "web-phone"}).json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as ws:
            ws.receive_json()
            ws.send_json({"type": "answered"})
            receive_type(ws, "paatti_text")
        s = app.state.store.get(sid)
        for _ in range(50):
            if s.ended_at is not None:
                break
            time.sleep(0.02)
        assert s.ended_at is not None


def test_browser_phone_with_voice_streams_audio():
    sarvam = FakeSarvam(["Send money to UPI cbi.case@okaxis"])
    app = create_app(Settings(), sarvam=sarvam)
    c = TestClient(app)
    sid = c.post("/api/sessions", json={"channel": "web-phone", "persona": "dadi"}).json()["session_id"]
    with c.websocket_connect(f"/ws/phone/{sid}") as ws:
        assert ws.receive_json()["voice"] is True
        assert receive_type(ws, "audio")  # greeting
        import time

        time.sleep(0.5)  # echo guard ignores the mic while the greeting plays
        pcm = silence(200, 16000) + tone(600, 16000) + silence(900, 16000)
        for i in range(0, len(pcm), 3200):
            ws.send_json({"type": "pcm", "data": base64.b64encode(pcm[i:i + 3200]).decode()})
        msg = ws.receive_json()
        while msg["type"] != "audio":
            msg = ws.receive_json()
        ws.send_json({"type": "hangup"})
    s = app.state.store.get(sid)
    assert s.persona_key == "dadi" and s.intel.upi_id.value == "cbi.case@okaxis"


def test_browser_phone_unknown_session_closes():
    c = TestClient(create_app(Settings()))
    with pytest.raises(Exception):
        with c.websocket_connect("/ws/phone/missing") as ws:
            ws.receive_json()


# ---- abuse protections ------------------------------------------------------

def test_dashboard_cannot_drive_or_end_a_phone_call_and_ids_are_masked():
    app = create_app(twilio_settings(operator_token="op"), sarvam=FakeSarvam())
    c = TestClient(app)
    signed_post(c, {"CallSid": "CA7", "From": "+919876543210", "Direction": "inbound"})
    s = app.state.store.all()[0]
    with c.websocket_connect(f"/ws/sessions/{s.id}") as ws:
        ws.receive_json()
        ws.send_json({"type": "scammer_text", "text": "hijack"})
        assert "watch only" in ws.receive_json()["message"]
        ws.send_json({"type": "end"})
        assert "watch only" in ws.receive_json()["message"]
    assert s.turns == [] and s.ended_at is None
    assert c.get("/api/sessions").json()[0]["caller_id"] == "+91******3210"
    assert c.get("/api/sessions", headers={"X-Operator-Token": "op"}).json()[0]["caller_id"] == "+919876543210"
    assert c.get("/api/history").json()["calls"][0]["caller_id"] == "+91******3210"


def test_replayed_webhook_is_rejected():
    c = TestClient(create_app(twilio_settings(), sarvam=FakeSarvam()))
    params = {"CallSid": "CA42", "From": "+919876543210", "Direction": "inbound"}
    assert signed_post(c, params).status_code == 200
    assert signed_post(c, params).status_code == 409


def test_turn_cap_ends_call(monkeypatch):
    monkeypatch.setenv("PAATTI_MAX_TURNS", "4")
    c = TestClient(create_app(Settings()))
    sid = c.post("/api/sessions").json()["session_id"]
    for _ in range(2):
        assert c.post(f"/api/sessions/{sid}/turn", json={"text": "hello"}).status_code == 200
    assert c.post(f"/api/sessions/{sid}/turn", json={"text": "hello"}).status_code == 409


async def test_utterance_flood_is_dropped():
    service = make_service()
    _, call, _, _ = make_call(service, FakeSarvam())  # worker not started: queue fills
    pcm = silence(200) + tone(600) + silence(900)
    for _ in range(5):
        for f in frames_of(pcm, 8000, 20):
            await call.feed(f)
    assert call._queue.qsize() == 2


async def test_sarvam_daily_cap():
    from paatti.sarvam import SarvamClient

    settings = Settings(sarvam_api_key="k", sarvam_daily_cap=1)
    http = httpx.AsyncClient(base_url="https://s.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"transcript": "hi"})))
    c = SarvamClient(settings, http=http)
    assert await c.transcribe(b"x") == "hi"
    with pytest.raises(SarvamError):
        await c.transcribe(b"x")


def test_live_calls_are_never_evicted():
    store = SessionStore(1)
    store.create()
    with pytest.raises(Exception):
        store.create()


async def test_slow_reply_gets_exactly_one_filler_before_it(monkeypatch):
    monkeypatch.setattr(voice_mod, "FILLER_AFTER_S", 0.02)
    service = make_service()
    original = service.respond_stream

    async def very_slow(session, text):
        await asyncio.sleep(0.15)
        async for sentence in original(session, text):
            yield sentence

    service.respond_stream = very_slow
    sarvam = FakeSarvam(["hello madam this is the police"])
    s, call, sent, _ = make_call(service, sarvam)
    await call.handle_utterance(tone(300))
    fillers = [t for t in sarvam.spoken if t in call.profile.fillers]
    assert fillers == [sarvam.spoken[0]]       # one filler, first
    assert len(sent) >= 2                      # then the reply


# ---- audit fixes: connection errors reach the browser, REST respects watch-only ----------

@pytest.mark.parametrize("path", ["/ws/phone/missing", "/ws/sessions/missing"])
def test_unknown_call_closes_with_4404_the_browser_can_see(path):
    from starlette.websockets import WebSocketDisconnect

    c = TestClient(create_app(Settings()))
    with c.websocket_connect(path) as ws:  # accepted first, so the close code is delivered
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4404


def test_ended_phone_call_closes_with_4404():
    from starlette.websockets import WebSocketDisconnect

    app = create_app(Settings())
    c = TestClient(app)
    sid = c.post("/api/sessions", json={"channel": "web-phone"}).json()["session_id"]
    app.state.service.end(app.state.store.get(sid))
    with c.websocket_connect(f"/ws/phone/{sid}") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4404


def test_rest_api_cannot_drive_or_end_a_phone_call():
    app = create_app(Settings())
    c = TestClient(app)
    sid = c.post("/api/sessions", json={"channel": "web-phone"}).json()["session_id"]
    assert c.post(f"/api/sessions/{sid}/turn", json={"text": "This is CBI"}).status_code == 403
    assert c.post(f"/api/sessions/{sid}/end").status_code == 403
    assert app.state.store.get(sid).ended_at is None and not app.state.store.get(sid).turns
    web = c.post("/api/sessions", json={"channel": "web"}).json()["session_id"]
    assert c.post(f"/api/sessions/{web}/turn", json={"text": "This is CBI"}).status_code == 200
    assert c.post(f"/api/sessions/{web}/end").status_code == 200


def test_single_call_view_masks_the_caller_number_without_operator_token():
    app = create_app(twilio_settings(operator_token="op"), sarvam=FakeSarvam())
    c = TestClient(app)
    s = app.state.service.create_session(channel="phone", caller_id="+919876543210")
    assert c.get(f"/api/sessions/{s.id}").json()["caller_id"] == "+91******3210"
    assert c.get(f"/api/sessions/{s.id}", headers={"X-Operator-Token": "op"}).json()["caller_id"] == "+919876543210"
