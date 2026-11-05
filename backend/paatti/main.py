"""FastAPI app: REST, live WebSockets, Twilio phone line, browser phone, static UI."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio import wav_from_pcm
from .config import Settings
from .db import Database, DbRepeatRegistry
from .extractor import LLMExtractor
from .persona import Persona
from .personas import PROFILES
from .report import GOLDEN_HOUR_S, build_report, render_pdf
from .sarvam import SarvamClient, SarvamError
from .service import CallService, Hub, RateLimiter, SessionStore
from .telephony import TelephonyBridge
from .voice import VoiceCall

log = logging.getLogger("paatti")

PHONE_RATE = 16000  # browser phone streams 16 kHz PCM16


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class SessionIn(BaseModel):
    channel: Literal["web", "web-phone"] = "web"
    persona: str | None = Field(default=None, max_length=20)


class DispatchIn(BaseModel):
    destination: Literal["bank_fraud_desk", "ncrp_1930"]


def mask_number(n: str | None) -> str | None:
    """+919876543210 -> +91******3210 for public listings."""
    if not n or len(n) < 6:
        return n
    return n[:3] + "*" * (len(n) - 7) + n[-4:]


DESTINATIONS = {
    "bank_fraud_desk": ("Bank fraud desk", "BFD"),
    "ncrp_1930": ("NCRP / 1930 helpline", "NCRP"),
}


def create_app(
    settings: Settings | None = None,
    persona: Persona | None = None,
    extractor: LLMExtractor | None = None,
    sarvam: SarvamClient | None = None,
    twilio_http=None,
    db: Database | None = None,
) -> FastAPI:
    settings = settings or Settings()
    persona = persona or Persona(settings)
    extractor = extractor or LLMExtractor(settings)
    if sarvam is None and settings.voice_enabled:
        sarvam = SarvamClient(settings)
    db = db or Database(settings.db_path)
    store = SessionStore(settings.max_sessions, registry=DbRepeatRegistry(db))
    hub = Hub()
    service = CallService(settings, persona, extractor, store, hub, db=db)
    limiter = RateLimiter(settings.sessions_per_minute)
    telephony = TelephonyBridge(settings, service, sarvam, http=twilio_http)

    app = FastAPI(title="Paatti", version="2.0")
    app.state.service = service
    app.state.store = store
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Operator-Token"],
    )
    app.include_router(telephony.router)

    # ---- REST --------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "persona": persona.mode,
            "extractor": f"{extractor.backend.name}+rules" if extractor.enabled else "rules",
            "voice": "sarvam" if sarvam else "browser",
            "telephony": settings.telephony_enabled,
            "twilio_number": settings.twilio_number if settings.telephony_enabled else None,
            "public_url": settings.public_url,
            "personas": [{"key": p.key, "name": p.name, "language": p.language_code} for p in PROFILES.values()],
            "default_persona": settings.persona_key if settings.persona_key in PROFILES else "paatti",
            "reply_sources": persona.stats,
            "cached_replies": len(persona.cache),
            "llm_pool": persona.backend.status() if hasattr(persona.backend, "status") else None,
        }

    def is_operator(request: Request) -> bool:
        tok = request.headers.get("X-Operator-Token") or request.query_params.get("token")
        return bool(settings.operator_token and tok and secrets.compare_digest(tok, settings.operator_token))

    @app.get("/api/history")
    def history(request: Request) -> dict:
        op = is_operator(request)
        calls = db.call_history()
        if not op:
            for c in calls:
                c["caller_id"] = mask_number(c["caller_id"])
        return {"calls": calls, "mule_registry": db.mule_registry(), "counts": db.counts()}

    @app.get("/api/sessions")
    def list_sessions(request: Request) -> list[dict]:
        op = is_operator(request)
        out = []
        for s in store.all()[:50]:
            row = {k: v for k, v in s.snapshot().items() if k not in ("intel", "stage_history", "dispatches")}
            if not op:
                row["caller_id"] = mask_number(row["caller_id"])
            out.append(row)
        return out

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request, body: SessionIn | None = None) -> dict:
        limiter.check(request.client.host if request.client else "unknown")
        body = body or SessionIn()
        return service.create_session(persona_key=body.persona, channel=body.channel).snapshot()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        s = store.get(session_id)
        return {**s.snapshot(), "turns": [t.model_dump(mode="json") for t in s.turns]}

    @app.post("/api/sessions/{session_id}/turn")
    async def post_turn(session_id: str, body: TurnIn) -> dict:
        s = store.get(session_id)
        if s.ended_at is not None:
            raise HTTPException(status_code=409, detail="call has ended")
        scammer_index = len(s.turns)
        paatti = await service.respond(s, body.text, background_extract=False)
        return {"scammer": s.turns[scammer_index].model_dump(mode="json"), "paatti": paatti, "state": s.snapshot()}

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(session_id: str) -> dict:
        s = store.get(session_id)
        service.end(s)
        return s.snapshot()

    @app.post("/api/sessions/{session_id}/dispatch", status_code=201)
    async def dispatch(session_id: str, body: DispatchIn) -> dict:
        """MOCK hand-off of the report. Records what would be sent; contacts no one."""
        s = store.get(session_id)
        if len(s.dispatches) >= 10:
            raise HTTPException(status_code=429, detail="too many dispatches for this call")
        report = build_report(s)
        label, prefix = DESTINATIONS[body.destination]
        first = s.first_capture_t()
        elapsed = (time.time() - (s.started_at + first)) if first is not None else None
        entry = {
            "destination": body.destination,
            "label": label,
            "reference": f"{prefix}-{secrets.token_hex(4).upper()}",
            "at": time.time(),
            "seconds_since_first_capture": round(elapsed, 1) if elapsed is not None else None,
            "within_golden_hour": elapsed is not None and elapsed <= GOLDEN_HOUR_S,
            "identifiers_sent": {k: v for k, v in report["ncrp_fields"].items() if v},
            "mock": True,
        }
        s.dispatches.append(entry)
        service.publish_state(s)
        return entry

    @app.get("/api/sessions/{session_id}/report.json")
    def report_json(session_id: str) -> JSONResponse:
        s = store.get(session_id)
        return JSONResponse(
            build_report(s), headers={"Content-Disposition": f'attachment; filename="mule-intel-{s.id}.json"'}
        )

    @app.get("/api/sessions/{session_id}/report.pdf")
    def report_pdf(session_id: str) -> Response:
        s = store.get(session_id)
        return Response(
            render_pdf(build_report(s)), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="mule-intel-{s.id}.pdf"'},
        )

    # ---- dashboard WebSocket: watch any call, drive text/push-to-talk calls ----
    def decode_b64(msg: dict, limit: int) -> bytes | None:
        try:
            data = base64.b64decode(msg.get("data", ""), validate=True)
        except (binascii.Error, ValueError):
            return None
        return data if 0 < len(data) <= limit else None

    @app.websocket("/ws/sessions/{session_id}")
    async def call_socket(ws: WebSocket, session_id: str) -> None:
        s = store.find(session_id)
        if s is None:
            await ws.close(code=4404)
            return
        await ws.accept()
        q = hub.subscribe(s.id)
        await ws.send_json({"type": "state", "state": s.snapshot()})
        for t in list(s.turns):  # late joiners (projector following a phone call) get the history
            await ws.send_json({"type": "turn", "turn": t.model_dump(mode="json")})
        # Phone calls are driven by the phone; dashboards may only watch them.
        read_only = s.channel != "web"

        async def pump() -> None:
            while True:
                await ws.send_json(await q.get())

        sender = asyncio.create_task(pump())

        def reply(msg: dict) -> None:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type") if isinstance(msg, dict) else None
                if s.ended_at is not None and kind not in ("end", "tick"):
                    reply({"type": "error", "message": "call has ended"})
                    continue
                if read_only and kind != "tick":
                    reply({"type": "error", "message": "this is a phone call: watch only"})
                    continue
                if kind == "scammer_text":
                    text = str(msg.get("text", "")).strip()
                    if not text or len(text) > settings.max_turn_chars:
                        reply({"type": "error", "message": "text must be 1-2000 characters"})
                        continue
                    try:
                        await service.respond(s, text)
                    except HTTPException as e:
                        reply({"type": "error", "message": e.detail})
                elif kind == "audio_chunk":
                    data = decode_b64(msg, settings.max_audio_bytes)
                    if data is None:
                        reply({"type": "error", "message": "invalid audio chunk"})
                        continue
                    s.add_audio_chunk(data)  # browser-voice mode: hashed for chain of custody only
                elif kind == "audio_utterance":
                    data = decode_b64(msg, settings.max_audio_bytes)
                    if data is None:
                        reply({"type": "error", "message": "invalid audio"})
                        continue
                    if not sarvam:
                        reply({"type": "error", "message": "server voice not configured; use browser voice or text"})
                        continue
                    s.add_audio_chunk(data)
                    try:
                        mime = str(msg.get("mime", "audio/wav")).split(";")[0].strip()[:40] or "audio/wav"
                        ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a",
                               "audio/mpeg": "mp3"}.get(mime, "wav")
                        text = await sarvam.transcribe(data, filename=f"turn.{ext}", mime=mime)
                    except SarvamError as e:
                        reply({"type": "notice", "message": f"speech-to-text failed: {e}; switch to text mode"})
                        continue
                    if text:
                        try:
                            paatti = await service.respond(s, text[: settings.max_turn_chars])
                        except HTTPException as e:
                            reply({"type": "error", "message": e.detail})
                            continue
                        await _speak_to(reply, s, paatti["text"])
                elif kind == "tick":
                    reply({"type": "state", "state": s.snapshot()})
                elif kind == "end":
                    service.end(s)
                else:
                    reply({"type": "error", "message": "unknown message type"})
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            hub.unsubscribe(s.id, q)

    async def _speak_to(reply, s, text: str) -> None:
        from .personas import get_profile

        profile = get_profile(s.persona_key)
        try:
            audio = await sarvam.synthesize(text, language_code=profile.language_code, speaker=profile.tts_speaker)
            reply({"type": "paatti_audio", "format": "wav", "data": base64.b64encode(audio).decode()})
        except SarvamError as e:
            reply({"type": "notice", "message": f"voice output unavailable: {e}"})

    # ---- browser phone: a mobile becomes the "scammer's phone" ----------------
    @app.websocket("/ws/phone/{session_id}")
    async def phone_socket(ws: WebSocket, session_id: str) -> None:
        s = store.find(session_id)
        if s is None or s.ended_at is not None:
            await ws.close(code=4404)
            return
        await ws.accept()
        send_lock = asyncio.Lock()

        async def send(msg: dict) -> None:
            async with send_lock:
                await ws.send_json(msg)

        async def send_audio(pcm: bytes) -> None:
            await send({"type": "audio", "format": "wav",
                        "data": base64.b64encode(wav_from_pcm(pcm, PHONE_RATE)).decode()})

        async def clear_audio() -> None:
            await send({"type": "clear"})

        call: VoiceCall | None = None
        if sarvam:
            call = VoiceCall(service, s, sarvam, in_rate=PHONE_RATE, out_rate=PHONE_RATE,
                             send_audio=send_audio, clear_audio=clear_audio, barge_in=False, echo_guard=True)
            call.start()
        await send({"type": "connected", "voice": bool(sarvam), "persona": s.persona_key})
        greeted = False

        async def greet() -> None:
            nonlocal greeted
            if greeted:
                return
            greeted = True
            if call:
                await call.greet()
            else:
                turn = service.say(s, PROFILES[s.persona_key].greeting)
                await send({"type": "paatti_text", "text": turn["text"], "lang": PROFILES[s.persona_key].language_code})

        try:
            if call:
                await call.warm()
            while True:
                try:
                    # The phone rings for a few seconds, then says "answered": greet then, so the
                    # greeting plays when the echo guard expects it. Fallback: greet after 5 s.
                    msg = await (ws.receive_json() if greeted else asyncio.wait_for(ws.receive_json(), 5))
                except asyncio.TimeoutError:
                    await greet()
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "answered":
                    await greet()
                elif kind == "pcm" and call and greeted:
                    data = decode_b64(msg, 512 * 1024)
                    if data:
                        s.add_audio_chunk(data)
                        await call.feed(data)
                elif kind == "text":
                    text = str(msg.get("text", "")).strip()[: settings.max_turn_chars]
                    if text:
                        try:
                            paatti = await service.respond(s, text)
                        except HTTPException:
                            break
                        if call:
                            await call.play(paatti["text"])
                        else:
                            await send({"type": "paatti_text", "text": paatti["text"],
                                        "lang": PROFILES[s.persona_key].language_code})
                elif kind == "hangup":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if call:
                await call.close()
            service.end(s)

    candidates = [Path(p) for p in (os.getenv("PAATTI_STATIC_DIR"),) if p]
    candidates += [Path(__file__).resolve().parents[2] / "frontend" / "dist", Path.cwd().parent / "frontend" / "dist"]
    dist = next((c for c in candidates if c.is_dir()), None)
    if dist:
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app


app = create_app()
