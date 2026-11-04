"""FastAPI app: REST for text mode and reports, WebSocket for the live call."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .extractor import LLMExtractor
from .persona import Persona
from .report import build_report, render_pdf
from .sarvam import SarvamClient, SarvamError
from .session import CallSession, RepeatRegistry

log = logging.getLogger("paatti")


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class SessionStore:
    def __init__(self, max_sessions: int):
        self.registry = RepeatRegistry()
        self._sessions: dict[str, CallSession] = {}
        self.max_sessions = max_sessions

    def create(self) -> CallSession:
        if len(self._sessions) >= self.max_sessions:
            # Drop the oldest ended session first, else the oldest overall.
            ended = [s for s in self._sessions.values() if s.ended_at is not None]
            victim = min(ended or self._sessions.values(), key=lambda s: s.started_at)
            self._sessions.pop(victim.id, None)
        s = CallSession(self.registry)
        self._sessions[s.id] = s
        return s

    def get(self, session_id: str) -> CallSession:
        s = self._sessions.get(session_id)
        if not s:
            raise HTTPException(status_code=404, detail="session not found")
        return s


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise HTTPException(status_code=429, detail="too many sessions, slow down")
        q.append(now)


def create_app(
    settings: Settings | None = None,
    persona: Persona | None = None,
    extractor: LLMExtractor | None = None,
    sarvam: SarvamClient | None = None,
) -> FastAPI:
    settings = settings or Settings()
    persona = persona or Persona(settings)
    extractor = extractor or LLMExtractor(settings)
    if sarvam is None and settings.voice_enabled:
        sarvam = SarvamClient(settings)
    store = SessionStore(settings.max_sessions)
    limiter = RateLimiter(settings.sessions_per_minute)

    app = FastAPI(title="Paatti", version="1.0")
    app.state.store = store
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    async def respond(session: CallSession, text: str) -> tuple[dict, dict]:
        text = text.strip()[: settings.max_turn_chars]
        with session.lock:
            session.add_scammer_turn(text)
            scammer = session.turns[-1]
        reply = await persona.reply(session)
        with session.lock:
            paatti = session.add_paatti_turn(reply)
        return scammer.model_dump(mode="json"), paatti.model_dump(mode="json")

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "persona": persona.mode,
            "extractor": "claude+rules" if extractor.enabled else "rules",
            "voice": "sarvam" if sarvam else "browser",
        }

    @app.post("/api/sessions", status_code=201)
    def create_session(request: Request) -> dict:
        limiter.check(request.client.host if request.client else "unknown")
        return store.create().snapshot()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        s = store.get(session_id)
        return {**s.snapshot(), "turns": [t.model_dump(mode="json") for t in s.turns]}

    @app.post("/api/sessions/{session_id}/turn")
    async def post_turn(session_id: str, body: TurnIn) -> dict:
        s = store.get(session_id)
        if s.ended_at is not None:
            raise HTTPException(status_code=409, detail="call has ended")
        scammer, paatti = await respond(s, body.text)
        await extractor.update(s)
        return {"scammer": scammer, "paatti": paatti, "state": s.snapshot()}

    @app.post("/api/sessions/{session_id}/end")
    def end_session(session_id: str) -> dict:
        s = store.get(session_id)
        s.end()
        return s.snapshot()

    @app.get("/api/sessions/{session_id}/report.json")
    def report_json(session_id: str) -> JSONResponse:
        s = store.get(session_id)
        return JSONResponse(
            build_report(s),
            headers={"Content-Disposition": f'attachment; filename="mule-intel-{s.id}.json"'},
        )

    @app.get("/api/sessions/{session_id}/report.pdf")
    def report_pdf(session_id: str) -> Response:
        s = store.get(session_id)
        return Response(
            render_pdf(build_report(s)), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="mule-intel-{s.id}.pdf"'},
        )

    @app.websocket("/ws/sessions/{session_id}")
    async def call_socket(ws: WebSocket, session_id: str) -> None:
        try:
            s = store.get(session_id)
        except HTTPException:
            await ws.close(code=4404)
            return
        await ws.accept()
        send_lock = asyncio.Lock()
        background: set[asyncio.Task] = set()

        async def send(msg: dict) -> None:
            async with send_lock:
                await ws.send_json(msg)

        async def run_extractor() -> None:
            try:
                if await extractor.update(s):
                    await send({"type": "state", "state": s.snapshot()})
            except Exception:  # never let a background failure kill the call
                log.exception("background extraction failed")

        async def handle_text(text: str) -> None:
            scammer, paatti = await respond(s, text)
            await send({"type": "turn", "turn": scammer})
            await send({"type": "state", "state": s.snapshot()})
            await send({"type": "turn", "turn": paatti})
            if sarvam:
                try:
                    audio = await sarvam.synthesize(paatti["text"])
                    await send({"type": "paatti_audio", "format": "wav", "data": base64.b64encode(audio).decode()})
                except SarvamError as e:
                    await send({"type": "notice", "message": f"voice output unavailable: {e}"})
            if extractor.enabled:
                task = asyncio.create_task(run_extractor())
                background.add(task)
                task.add_done_callback(background.discard)

        def decode_audio(msg: dict) -> bytes | None:
            try:
                data = base64.b64decode(msg.get("data", ""), validate=True)
            except (binascii.Error, ValueError):
                return None
            return data if 0 < len(data) <= settings.max_audio_bytes else None

        await send({"type": "state", "state": s.snapshot()})
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type") if isinstance(msg, dict) else None
                if s.ended_at is not None and kind != "end":
                    await send({"type": "error", "message": "call has ended"})
                    continue
                if kind == "scammer_text":
                    text = str(msg.get("text", "")).strip()
                    if not text or len(text) > settings.max_turn_chars:
                        await send({"type": "error", "message": "text must be 1-2000 characters"})
                        continue
                    await handle_text(text)
                elif kind == "audio_chunk":
                    # Browser-voice mode: raw mic audio, hashed for chain of custody only.
                    data = decode_audio(msg)
                    if data is None:
                        await send({"type": "error", "message": "invalid audio chunk"})
                        continue
                    s.add_audio_chunk(data)
                elif kind == "audio_utterance":
                    data = decode_audio(msg)
                    if data is None:
                        await send({"type": "error", "message": "invalid audio"})
                        continue
                    if not sarvam:
                        await send({"type": "error", "message": "server voice not configured; use browser voice or text"})
                        continue
                    s.add_audio_chunk(data)
                    try:
                        text = await sarvam.transcribe(data, mime=str(msg.get("mime", "audio/wav"))[:40])
                    except SarvamError as e:
                        await send({"type": "notice", "message": f"speech-to-text failed: {e}; switch to text mode"})
                        continue
                    if text:
                        await handle_text(text[: settings.max_turn_chars])
                elif kind == "tick":
                    await send({"type": "state", "state": s.snapshot()})
                elif kind == "end":
                    s.end()
                    await send({"type": "state", "state": s.snapshot()})
                    break
                else:
                    await send({"type": "error", "message": "unknown message type"})
        except WebSocketDisconnect:
            pass
        finally:
            for task in list(background):
                task.cancel()

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app


app = create_app()
