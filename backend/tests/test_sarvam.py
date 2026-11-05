import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from paatti.config import Settings
from paatti.main import create_app
from paatti.sarvam import SarvamClient, SarvamError


def make_client(handler):
    settings = Settings(sarvam_api_key="test-key")
    http = httpx.AsyncClient(base_url="https://sarvam.test", transport=httpx.MockTransport(handler))
    return SarvamClient(settings, http=http)


def ok_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["api-subscription-key"] == "test-key"
    if request.url.path == "/speech-to-text":
        assert b"saaras:v3" in request.content and b"translit" in request.content
        return httpx.Response(200, json={"transcript": "Transfer to account 3456 7890 1234"})
    body = json.loads(request.content)
    assert body["model"] == "bulbul:v3" and body["target_language_code"] == "ta-IN"
    return httpx.Response(200, json={"audios": [base64.b64encode(b"WAVDATA").decode()]})


def test_requires_key():
    with pytest.raises(SarvamError):
        SarvamClient(Settings(sarvam_api_key=None))


@pytest.mark.asyncio
async def test_transcribe_and_synthesize():
    c = make_client(ok_handler)
    assert await c.transcribe(b"audio") == "Transfer to account 3456 7890 1234"
    assert await c.synthesize("Aiyo kanna") == b"WAVDATA"


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
