"""Real phone calls via Twilio Programmable Voice + bidirectional Media Streams.

Inbound:  caller dials the Paatti number -> Twilio POSTs /telephony/twilio/voice
          -> we answer with <Connect><Stream> -> audio flows over the WebSocket.
Outbound: operator clicks "Ring phone" -> Twilio calls an allowlisted number
          -> same webhook and stream when they pick up.

Security: webhook requests must carry a valid X-Twilio-Signature; each stream
must present a one-time token minted in that signed webhook; outbound calls
need the operator token and only reach allowlisted numbers.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from urllib.parse import parse_qsl
from xml.sax.saxutils import quoteattr

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .audio import mulaw_decode, mulaw_encode
from .config import Settings
from .sarvam import SarvamClient
from .service import CallService, RateLimiter
from .voice import VoiceCall

log = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01"
TOKEN_TTL_S = 120
OUT_CHUNK = 8000  # 1 s of 8 kHz mu-law per media message


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def normalize_e164(number: str) -> str:
    digits = re.sub(r"[^\d+]", "", number or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+") and len(digits) == 10:
        digits = "+91" + digits  # bare Indian mobile
    elif not digits.startswith("+"):
        digits = "+" + digits
    return digits


class OutboundIn(BaseModel):
    to: str = Field(min_length=8, max_length=20)
    persona: str | None = Field(default=None, max_length=20)


class TelephonyBridge:
    def __init__(self, settings: Settings, service: CallService, sarvam: SarvamClient | None,
                 http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.service = service
        self.sarvam = sarvam
        self.http = http
        self._tokens: dict[str, tuple[str, float]] = {}
        self.limiter = RateLimiter(10)
        self.router = self._build_router()

    # ---- helpers ---------------------------------------------------------
    def _mint_token(self, session_id: str) -> str:
        now = time.monotonic()
        self._tokens = {t: v for t, v in self._tokens.items() if v[1] > now}
        token = secrets.token_urlsafe(24)
        self._tokens[token] = (session_id, now + TOKEN_TTL_S)
        return token

    def _redeem(self, token: str | None) -> str | None:
        if not token:
            return None
        entry = self._tokens.pop(token, None)
        if not entry or entry[1] < time.monotonic():
            return None
        return entry[0]

    def _public(self, path: str) -> str:
        return f"{self.settings.public_url}{path}"

    def _check_signature(self, request: Request, params: dict[str, str]) -> None:
        url = self._public(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
        expected = twilio_signature(self.settings.twilio_auth_token or "", url, params)
        got = request.headers.get("X-Twilio-Signature", "")
        if not hmac.compare_digest(expected, got):
            raise HTTPException(status_code=403, detail="invalid Twilio signature")

    def _twiml_stream(self, token: str) -> str:
        ws_url = self._public("/telephony/twilio/stream").replace("https://", "wss://").replace("http://", "ws://")
        return (
            '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
            f"<Stream url={quoteattr(ws_url)}><Parameter name=\"token\" value={quoteattr(token)}/></Stream>"
            "</Connect></Response>"
        )

    # ---- routes ----------------------------------------------------------
    def _build_router(self) -> APIRouter:
        r = APIRouter()

        @r.post("/telephony/twilio/voice")
        async def voice_webhook(request: Request) -> Response:
            if not self.settings.telephony_enabled:
                raise HTTPException(status_code=503, detail="telephony not configured")
            params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
            self._check_signature(request, params)
            if not self.sarvam:
                twiml = ('<?xml version="1.0" encoding="UTF-8"?><Response><Say>Voice service not configured.</Say>'
                         "<Hangup/></Response>")
                return Response(twiml, media_type="application/xml")
            outbound = params.get("Direction", "").startswith("outbound")
            # The "scammer" is whoever is on the other end: caller (inbound) or callee (outbound).
            other_party = params.get("To") if outbound else params.get("From")
            persona = request.query_params.get("persona")
            session = self.service.create_session(persona_key=persona, channel="phone", caller_id=other_party)
            return Response(self._twiml_stream(self._mint_token(session.id)), media_type="application/xml")

        @r.websocket("/telephony/twilio/stream")
        async def media_stream(ws: WebSocket) -> None:
            await ws.accept()
            await self._handle_stream(ws)

        @r.post("/api/telephony/call")
        async def outbound(body: OutboundIn, request: Request,
                           x_operator_token: str | None = Header(default=None)) -> dict:
            if not self.settings.telephony_enabled or not self.settings.twilio_number:
                raise HTTPException(status_code=503, detail="telephony not configured")
            if not self.settings.operator_token:
                raise HTTPException(status_code=403, detail="set PAATTI_OPERATOR_TOKEN to enable outbound calls")
            if not x_operator_token or not hmac.compare_digest(x_operator_token, self.settings.operator_token):
                raise HTTPException(status_code=403, detail="operator token required")
            self.limiter.check(request.client.host if request.client else "unknown")
            to = normalize_e164(body.to)
            allowed = {normalize_e164(n) for n in self.settings.outbound_allowlist}
            if to not in allowed:
                raise HTTPException(status_code=403, detail="number not in PAATTI_OUTBOUND_ALLOWLIST")
            url = self._public("/telephony/twilio/voice")
            if body.persona:
                url += f"?persona={body.persona}"
            http = self.http or httpx.AsyncClient(timeout=15)
            try:
                resp = await http.post(
                    f"{TWILIO_API}/Accounts/{self.settings.twilio_account_sid}/Calls.json",
                    data={"To": to, "From": self.settings.twilio_number, "Url": url, "Method": "POST"},
                    auth=(self.settings.twilio_account_sid or "", self.settings.twilio_auth_token or ""),
                )
            except httpx.HTTPError as e:
                raise HTTPException(status_code=502, detail=f"could not reach Twilio: {type(e).__name__}")
            finally:
                if self.http is None:
                    await http.aclose()
            if resp.status_code >= 300:
                log.warning("Twilio create call HTTP %s", resp.status_code)
                raise HTTPException(status_code=502, detail=f"Twilio refused the call ({resp.status_code})")
            return {"status": "ringing", "call_sid": resp.json().get("sid"), "to": to}

        return r

    async def _handle_stream(self, ws: WebSocket) -> None:
        call: VoiceCall | None = None
        session = None
        stream_sid = None
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event = msg.get("event")
                if event == "start":
                    start = msg.get("start") or {}
                    stream_sid = start.get("streamSid") or msg.get("streamSid")
                    session_id = self._redeem((start.get("customParameters") or {}).get("token"))
                    session = self.service.store.find(session_id) if session_id else None
                    if session is None or self.sarvam is None:
                        await ws.close(code=4403)
                        return
                    call = self._make_call(ws, session, stream_sid)
                    call.start()
                    await call.warm()
                    await call.greet()
                elif event == "media" and call is not None and session is not None:
                    media = msg.get("media") or {}
                    if media.get("track", "inbound") != "inbound":
                        continue
                    try:
                        payload = base64.b64decode(media.get("payload", ""), validate=True)
                    except (binascii.Error, ValueError):
                        continue
                    session.add_audio_chunk(payload)  # raw mu-law as received: chain of custody
                    await call.feed(mulaw_decode(payload))
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if call:
                await call.close()
            if session is not None:
                self.service.end(session)

    def _make_call(self, ws: WebSocket, session, stream_sid: str | None) -> VoiceCall:
        counter = {"n": 0}

        async def send_audio(pcm: bytes) -> None:
            ulaw = mulaw_encode(pcm)
            for i in range(0, len(ulaw), OUT_CHUNK):
                await ws.send_text(json.dumps({
                    "event": "media", "streamSid": stream_sid,
                    "media": {"payload": base64.b64encode(ulaw[i:i + OUT_CHUNK]).decode()},
                }))
            counter["n"] += 1
            await ws.send_text(json.dumps({"event": "mark", "streamSid": stream_sid,
                                           "mark": {"name": f"reply-{counter['n']}"}}))

        async def clear_audio() -> None:
            await ws.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))

        assert self.sarvam is not None
        return VoiceCall(self.service, session, self.sarvam, in_rate=8000, out_rate=8000,
                         send_audio=send_audio, clear_audio=clear_audio, barge_in=True)
