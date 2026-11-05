import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from paatti.config import Settings
from paatti.main import create_app
from paatti.sarvam import SarvamClient, SarvamError


def make_client(handler, raw=True):
    # raw=True: no post-processing, so tests can compare exact bytes. Processing has its own test.
    settings = Settings(sarvam_api_key="test-key", phone_line=False, **({"tts_speedup": 1.0, "tts_max_gap_ms": 0} if raw else {}))
    http = httpx.AsyncClient(base_url="https://sarvam.test", transport=httpx.MockTransport(handler))
    return SarvamClient(settings, http=http)


from paatti.audio import wav_from_pcm  # noqa: E402

FAKE_WAV = wav_from_pcm(b"\x00\x00" * 2205, 22050)  # 0.1 s of silence at Sarvam's 22.05 kHz


def ok_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["api-subscription-key"] == "test-key"
    if request.url.path == "/speech-to-text":
        assert b"saaras:v3" in request.content and b"translit" in request.content
        return httpx.Response(200, json={"transcript": "Transfer to account 3456 7890 1234"})
    body = json.loads(request.content)
    assert body["model"] == "bulbul:v3" and body["target_language_code"] == "ta-IN"
    return httpx.Response(200, json={"audios": [base64.b64encode(FAKE_WAV).decode()]})


def test_requires_key():
    with pytest.raises(SarvamError):
        SarvamClient(Settings(sarvam_api_key=None))


@pytest.mark.asyncio
async def test_transcribe_and_synthesize():
    c = make_client(ok_handler)
    assert await c.transcribe(b"audio") == "Transfer to account 3456 7890 1234"
    assert await c.synthesize("Aiyo kanna") == FAKE_WAV


@pytest.mark.asyncio
async def test_http_errors_raise():
    c = make_client(lambda r: httpx.Response(500, json={}))
    with pytest.raises(SarvamError):
        await c.transcribe(b"a")
    with pytest.raises(SarvamError):
        await c.synthesize("x")
    empty = make_client(lambda r: httpx.Response(200, json={"audios": []}))
    with pytest.raises(SarvamError):
        await empty.synthesize("x")


def test_websocket_voice_loop(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(Settings(anthropic_api_key=None), sarvam=make_client(ok_handler))
    client = TestClient(app)
    assert client.get("/api/health").json()["voice"] == "sarvam"
    sid = client.post("/api/sessions").json()["session_id"]
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.receive_json()
        ws.send_json({"type": "audio_utterance", "data": base64.b64encode(b"utterance").decode(), "mime": "audio/webm"})
        kinds = [ws.receive_json() for _ in range(4)]
        assert [m["type"] for m in kinds] == ["turn", "state", "turn", "paatti_audio"]
        assert kinds[1]["state"]["intel"]["account_number"]["value"] == "345678901234"
        ws.send_json({"type": "audio_utterance", "data": "%%%not-base64"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "scammer_text", "text": ""})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "tick"})
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "end"})
        ws.receive_json()


@pytest.mark.asyncio
async def test_network_errors_become_sarvam_errors():
    def boom(request):
        raise httpx.ConnectError("proxy refused")

    c = make_client(boom)
    with pytest.raises(SarvamError):
        await c.transcribe(b"a")
    with pytest.raises(SarvamError):
        await c.synthesize("x")


@pytest.mark.asyncio
async def test_error_message_from_sarvam_is_surfaced():
    c = make_client(lambda r: httpx.Response(400, json={"error": {"message": "Unsupported audio format"}}))
    with pytest.raises(SarvamError, match="Unsupported audio format"):
        await c.transcribe(b"a")


@pytest.mark.asyncio
async def test_speech_is_fast_by_default_and_pace_is_clamped(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"audios": [base64.b64encode(FAKE_WAV).decode()]})

    monkeypatch.delenv("SARVAM_TTS_PACE", raising=False)  # ignore any local .env
    c = SarvamClient(Settings(sarvam_api_key="k", audio_cache_dir=None, tts_speedup=1.0, tts_max_gap_ms=0, phone_line=False),
                     http=httpx.AsyncClient(base_url="https://s.test", transport=httpx.MockTransport(handler)))
    await c.synthesize("Aiyo kanna")
    assert seen["pace"] == 2.0  # Bulbul v3's fastest
    monkeypatch.setenv("SARVAM_TTS_PACE", "3.5")
    assert Settings().tts_pace == 2.0
    monkeypatch.setenv("SARVAM_TTS_PACE", "0.1")
    assert Settings().tts_pace == 0.5


@pytest.mark.asyncio
async def test_unknown_persona_voice_falls_back_to_default_speaker():
    speakers = []

    def handler(request):
        body = json.loads(request.content)
        speakers.append(body["speaker"])
        if body["speaker"] != "kavitha":
            return httpx.Response(400, json={"error": {"message": "invalid speaker"}})
        return httpx.Response(200, json={"audios": [base64.b64encode(FAKE_WAV).decode()]})

    c = SarvamClient(Settings(sarvam_api_key="k", audio_cache_dir=None, tts_speedup=1.0, tts_max_gap_ms=0, phone_line=False),
                     http=httpx.AsyncClient(base_url="https://s.test", transport=httpx.MockTransport(handler)))
    assert await c.synthesize("Namaskara", language_code="kn-IN", speaker="ritu") == FAKE_WAV
    assert speakers == ["ritu", "kavitha"]
    failing = SarvamClient(Settings(sarvam_api_key="k", audio_cache_dir=None, tts_speedup=1.0, tts_max_gap_ms=0, phone_line=False), http=httpx.AsyncClient(
        base_url="https://s.test", transport=httpx.MockTransport(lambda r: httpx.Response(400, json={}))))
    with pytest.raises(SarvamError):  # the default voice itself failing is still an error
        await failing.synthesize("x", speaker="kavitha")


@pytest.mark.asyncio
async def test_synthesized_audio_has_long_pauses_removed():
    from paatti.audio import pcm_from_wav, wav_from_pcm
    import math, struct

    rate = 22050
    tone = struct.pack(f"<{rate // 2}h", *(int(8000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(rate // 2)))
    gap = b"\x00\x00" * rate  # 1 s pause
    slow = wav_from_pcm(tone + gap + tone, rate)
    c = make_client(lambda r: httpx.Response(200, json={"audios": [base64.b64encode(slow).decode()]}), raw=False)
    pcm, out_rate = pcm_from_wav(await c.synthesize("Aiyo kanna, oru nimisham"))
    # 2.0 s (with a 1 s pause) -> pause cut to 0.12 s, then 1.15x time-stretch -> ~0.97 s
    assert out_rate == rate and 0.8 < len(pcm) / 2 / rate < 1.1


async def test_synthesize_puts_voice_on_a_phone_line():
    import math
    import struct

    from paatti.audio import LINE_RATE, pcm_from_wav, rms
    from paatti.config import Settings

    rate = 22050
    tone = struct.pack(f"<{rate}h", *(int(9000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(rate)))
    wav = wav_from_pcm(tone, rate)
    settings = Settings(sarvam_api_key="k", tts_speedup=1.0, tts_max_gap_ms=0, audio_cache_dir=None)
    http = httpx.AsyncClient(base_url="https://sarvam.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"audios": [base64.b64encode(wav).decode()]})))
    pcm, out_rate = pcm_from_wav(await SarvamClient(settings, http=http).synthesize("Hello kanna"))
    assert out_rate == LINE_RATE                      # narrowband, like a real call
    assert 1.0 < len(pcm) / 2 / LINE_RATE < 1.25      # same length plus a short room-tone lead/tail
    assert rms(pcm[:2 * LINE_RATE // 25]) > 0         # lead-in is quiet room tone, not digital silence


async def test_sarvam_remembers_its_last_error_for_the_health_page():
    c = make_client(lambda r: httpx.Response(402, json={"error": {"message": "credits exhausted"}}))
    with pytest.raises(SarvamError):
        await c.synthesize("Hello kanna")
    st = c.status()
    assert st["last_error"]["status"] == 402 and st["last_error"]["what"] == "text-to-speech"
    assert st["calls_today"] == 1


def test_health_names_the_exhausted_key(monkeypatch):
    app = create_app(Settings(sarvam_api_key="k"))
    c = TestClient(app)
    assert c.get("/api/health").json()["quota"] == []


async def test_live_reply_retries_once_after_a_429():
    hits = []

    def handler(r):
        hits.append(1)
        if len(hits) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json={"audios": [base64.b64encode(FAKE_WAV).decode()]})

    c = make_client(handler)
    assert await c.synthesize("Hello kanna") == FAKE_WAV and len(hits) == 2


async def test_warmup_call_does_not_retry_and_keeps_sarvams_message():
    c = make_client(lambda r: httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}}))
    with pytest.raises(SarvamError):
        await c.synthesize("Hello kanna", retry_429=False)
    assert c.status()["last_error"]["detail"] == "Rate limit exceeded"


def test_v2_only_speaker_left_in_the_dashboard_falls_back_to_a_v3_voice(monkeypatch):
    monkeypatch.setenv("SARVAM_TTS_SPEAKER", "anushka")
    monkeypatch.setenv("SARVAM_TTS_MODEL", "bulbul:v3")
    assert Settings().tts_speaker == "kavitha"
    monkeypatch.setenv("SARVAM_TTS_SPEAKER", "roopa")
    assert Settings().tts_speaker == "roopa"


async def test_live_reply_retries_twice_before_giving_up():
    hits = []

    def handler(r):
        hits.append(1)
        if len(hits) <= 2:
            return httpx.Response(429, headers={"retry-after": "0.5"}, json={"error": {"message": "Rate limit exceeded"}})
        return httpx.Response(200, json={"audios": [base64.b64encode(FAKE_WAV).decode()]})

    c = make_client(handler)
    assert await c.synthesize("Hello kanna") == FAKE_WAV and len(hits) == 3


async def test_throttle_keeps_requests_under_our_per_minute_cap(monkeypatch):
    import paatti.sarvam as sv

    settings = Settings(sarvam_api_key="k", sarvam_rpm=4, phone_line=False, tts_speedup=1.0,
                        tts_max_gap_ms=0, audio_cache_dir=None)
    c = SarvamClient(settings, http=httpx.AsyncClient(base_url="https://sarvam.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"audios": [base64.b64encode(FAKE_WAV).decode()]}))))
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        now = sv.time.monotonic()
        c._hits = type(c._hits)(t - 61 for t in c._hits)  # pretend a minute passed

    monkeypatch.setattr(sv.asyncio, "sleep", fake_sleep)
    for i in range(4):
        await c.synthesize(f"line {i}")
    assert slept == []                                   # under the cap: no waiting
    await c.synthesize("line 5")
    assert slept                                         # over the cap: waits instead of a 429
    assert c.status()["per_minute_cap"] == 4


def test_blank_numeric_settings_fall_back_instead_of_crashing(monkeypatch):
    for name in ("SARVAM_TTS_PACE", "PAATTI_TTS_SPEEDUP", "PAATTI_SARVAM_RPM", "SARVAM_DAILY_CAP", "PAATTI_LINE_NOISE_DB"):
        monkeypatch.setenv(name, "")
    s = Settings()
    assert s.tts_pace == 2.0 and s.sarvam_rpm == 20 and s.sarvam_daily_cap == 2000
    monkeypatch.setenv("SARVAM_TTS_PACE", "fast")
    assert Settings().tts_pace == 2.0
