"""Sarvam AI speech clients: Saaras (speech-to-text) and Bulbul (text-to-speech).

Endpoints, model names and the speaker are configurable via environment
variables (see config.py) so they can be adjusted to the account's API
version without code changes. Used only when SARVAM_API_KEY is set; otherwise
the browser's built-in speech APIs handle the voice loop.
"""

from __future__ import annotations

import base64
import logging

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class SarvamError(RuntimeError):
    pass


class SarvamClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        if not settings.sarvam_api_key:
            raise SarvamError("SARVAM_API_KEY not configured")
        self.settings = settings
        self.http = http or httpx.AsyncClient(base_url=settings.sarvam_base_url, timeout=15)

    @property
    def _headers(self) -> dict[str, str]:
        return {"api-subscription-key": self.settings.sarvam_api_key or ""}

    async def transcribe(self, audio: bytes, filename: str = "turn.wav", mime: str = "audio/wav") -> str:
        """Speech-to-text on one caller utterance (Tamil/English code-mixed)."""
        resp = await self.http.post(
            "/speech-to-text",
            headers=self._headers,
            files={"file": (filename, audio, mime)},
            data={"model": self.settings.stt_model, "language_code": "unknown"},
        )
        if resp.status_code != 200:
            log.warning("Sarvam STT HTTP %s", resp.status_code)
            raise SarvamError(f"speech-to-text failed ({resp.status_code})")
        return str(resp.json().get("transcript", "")).strip()

    async def synthesize(self, text: str) -> bytes:
        """Text-to-speech in a slow, elderly Tamil voice. Returns WAV bytes."""
        resp = await self.http.post(
            "/text-to-speech",
            headers=self._headers,
            json={
                "text": text[:1500],
                "target_language_code": "ta-IN",
                "model": self.settings.tts_model,
                "speaker": self.settings.tts_speaker,
                "pace": self.settings.tts_pace,
            },
        )
        if resp.status_code != 200:
            log.warning("Sarvam TTS HTTP %s", resp.status_code)
            raise SarvamError(f"text-to-speech failed ({resp.status_code})")
        audios = resp.json().get("audios") or []
        if not audios:
            raise SarvamError("text-to-speech returned no audio")
        return base64.b64decode(audios[0])
