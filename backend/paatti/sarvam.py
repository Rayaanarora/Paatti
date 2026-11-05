"""Sarvam AI speech clients: Saaras (speech-to-text) and Bulbul (text-to-speech).

Endpoints, model names and the speaker are configurable via environment
variables (see config.py) so they can be adjusted to the account's API
version without code changes. Used only when SARVAM_API_KEY is set; otherwise
the browser's built-in speech APIs handle the voice loop.
"""

from __future__ import annotations

import base64
import wave
import hashlib
import logging
import time
from pathlib import Path

import httpx

from .config import Settings
from .audio import LINE_RATE, compress_silence, pcm_from_wav, time_stretch, wav_from_pcm, phone_line
from .speech import normalize_for_tts

log = logging.getLogger(__name__)


class SarvamError(RuntimeError):
    pass


def _detail(resp: httpx.Response) -> str:
    """Sarvam's own error message (no secrets), so failures are diagnosable from the UI."""
    try:
        msg = resp.json().get("error", {}).get("message", "")
    except ValueError:
        msg = ""
    return f": {str(msg)[:160]}" if msg else ""


class SarvamClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        if not settings.sarvam_api_key:
            raise SarvamError("SARVAM_API_KEY not configured")
        self.settings = settings
        self._day = time.strftime("%Y-%m-%d")
        self.calls_today = 0
        self.last_error: dict | None = None  # {"status", "at", "what"}: surfaced in /api/health
        self.http = http or httpx.AsyncClient(base_url=settings.sarvam_base_url, timeout=15)

    def _note(self, status: int, what: str) -> None:
        self.last_error = {"status": status, "at": time.time(), "what": what}

    def status(self) -> dict:
        return {"calls_today": self.calls_today, "daily_cap": self.settings.sarvam_daily_cap,
                "last_error": self.last_error}

    def _spend(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self.calls_today = today, 0
        if self.calls_today >= self.settings.sarvam_daily_cap:
            raise SarvamError("daily Sarvam cap reached")
        self.calls_today += 1

    def _stt_fields(self) -> dict[str, str]:
        # "translit" returns romanised text with spoken digits as numerals (verified live), which
        # the instant rule extractor can read; native script would only reach the LLM extractor.
        fields = {"model": self.settings.stt_model, "language_code": "unknown"}
        if self.settings.stt_mode:
            fields["mode"] = self.settings.stt_mode
        return fields

    @property
    def _headers(self) -> dict[str, str]:
        return {"api-subscription-key": self.settings.sarvam_api_key or ""}

    async def transcribe(self, audio: bytes, filename: str = "turn.wav", mime: str = "audio/wav") -> str:
        """Speech-to-text on one caller utterance (Tamil/English code-mixed)."""
        self._spend()
        clean_mime = (mime or "audio/wav").split(";")[0].strip().lower()
        if not filename or filename == "turn.wav":
            if "webm" in clean_mime:
                filename = "turn.webm"
            elif "mp4" in clean_mime or "m4a" in clean_mime:
                filename = "turn.mp4"
            elif "ogg" in clean_mime or "opus" in clean_mime:
                filename = "turn.ogg"
            else:
                filename = "turn.wav"

        try:
            resp = await self.http.post(
                "/speech-to-text",
                headers=self._headers,
                files={"file": (filename, audio, clean_mime)},
                data=self._stt_fields(),
            )
        except httpx.HTTPError as e:  # network trouble must surface as SarvamError, never kill a call
            raise SarvamError(f"speech-to-text unreachable ({type(e).__name__})") from e
        if resp.status_code != 200:
            self._note(resp.status_code, "speech-to-text")
            log.warning("Sarvam STT HTTP %s: %s", resp.status_code, resp.text)
            raise SarvamError(f"speech-to-text failed ({resp.status_code}{_detail(resp)})")
        return str(resp.json().get("transcript", "")).strip()

    async def synthesize(self, text: str, language_code: str = "ta-IN", speaker: str | None = None) -> bytes:
        """Text-to-speech in a slow, elderly voice. Returns WAV bytes."""
        text = normalize_for_tts(text)  # '...' -> short pause: natural pacing
        cached = self.cached(text, language_code, speaker)
        if cached is not None:
            return self._on_line(cached)

        self._spend()
        body = {
            "text": text[:1500],
            "target_language_code": language_code,
            "model": self.settings.tts_model,
            "speaker": speaker or self.settings.tts_speaker,
            "pace": self.settings.tts_pace,
        }
        if self.settings.tts_sample_rate:
            body["speech_sample_rate"] = self.settings.tts_sample_rate
        try:
            resp = await self.http.post("/text-to-speech", headers=self._headers, json=body)
        except httpx.HTTPError as e:
            raise SarvamError(f"text-to-speech unreachable ({type(e).__name__})") from e
        fallback = self.settings.tts_speaker
        if resp.status_code in (400, 422) and speaker and speaker != fallback:
            # A persona voice the account/model doesn't offer: speak with the default voice rather than go silent.
            log.warning("Sarvam rejected speaker %r (HTTP %s); using %r", speaker, resp.status_code, fallback)
            return await self.synthesize(text, language_code=language_code, speaker=fallback)
        if resp.status_code != 200:
            self._note(resp.status_code, "text-to-speech")
            log.warning("Sarvam TTS HTTP %s", resp.status_code)
            raise SarvamError(f"text-to-speech failed ({resp.status_code}{_detail(resp)})")
        audios = resp.json().get("audios") or []
        if not audios:
            raise SarvamError("text-to-speech returned no audio")
        audio_bytes = self._tighten(base64.b64decode(audios[0]))
        self._store(text, language_code, speaker, audio_bytes)
        return self._on_line(audio_bytes)

    def _on_line(self, wav: bytes) -> bytes:
        """Put the clean clip on a 'phone line' (applied on every play, so the cache stays clean and
        the room tone is fresh each time)."""
        if not self.settings.phone_line:
            return wav
        try:
            pcm, rate = pcm_from_wav(wav)
            return wav_from_pcm(phone_line(pcm, rate, noise_db=self.settings.line_noise_db), LINE_RATE)
        except (EOFError, ValueError, wave.Error):
            return wav

    def _tighten(self, wav: bytes) -> bytes:
        """Make the clip sound like natural, quick speech: cut long pauses, then speed it up with a
        pitch-preserving time-stretch (Sarvam's own pace is already at its max). Falls back to the
        original clip on any decoding issue."""
        if self.settings.tts_max_gap_ms <= 0 and abs(self.settings.tts_speedup - 1.0) < 0.01:
            return wav
        try:
            pcm, rate = pcm_from_wav(wav)
            if self.settings.tts_max_gap_ms > 0:
                pcm = compress_silence(pcm, rate, max_gap_ms=self.settings.tts_max_gap_ms)
            pcm = time_stretch(pcm, rate, self.settings.tts_speedup)  # faster, same pitch
            return wav_from_pcm(pcm, rate)
        except (EOFError, ValueError, wave.Error):
            return wav

    # ---- disk audio cache (pre-generated lines play with zero latency) -------
    def _cache_file(self, text: str, language_code: str, speaker: str | None) -> Path | None:
        if not self.settings.audio_cache_dir:
            return None
        speaker = speaker or self.settings.tts_speaker
        text = text.strip()
        raw = (f"{language_code}|{speaker}|{self.settings.tts_pace}|gap{self.settings.tts_max_gap_ms}|"
               f"x{self.settings.tts_speedup:.2f}|"
               f"{normalize_for_tts(text)}")
        return Path(self.settings.audio_cache_dir) / f"{hashlib.sha256(raw.encode()).hexdigest()[:16]}.wav"

    def cached(self, text: str, language_code: str = "ta-IN", speaker: str | None = None) -> bytes | None:
        f = self._cache_file(text, language_code, speaker)
        if f is not None and f.exists():
            try:
                return f.read_bytes()
            except OSError:
                return None
        return None

    def _store(self, text: str, language_code: str, speaker: str | None, audio: bytes) -> None:
        f = self._cache_file(text, language_code, speaker)
        if f is None or not self.settings.audio_cache_write:
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(audio)
        except OSError:
            log.warning("could not write audio cache")
