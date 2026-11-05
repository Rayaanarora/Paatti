"""Runtime configuration from environment variables. No secrets in code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env from backend/.env or repo root .env
    for candidate in (Path(__file__).resolve().parents[1] / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
except ImportError:
    pass


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _env_num(name: str, default: str, cast=float):
    """Read a numeric setting; an empty or malformed value (e.g. a blank row left in the hosting
    dashboard) falls back to the default instead of crashing the server at startup."""
    raw = (os.getenv(name) or "").strip()
    try:
        return cast(raw) if raw else cast(default)
    except ValueError:
        return cast(default)


# Voices bulbul:v2 had that bulbul:v3 rejects (HTTP 400). An old value left in the hosting
# dashboard would break every default-voice request, so it falls back to a v3 voice.
_V2_ONLY_SPEAKERS = {"anushka", "manisha", "vidya", "arya", "abhilash", "karun", "hitesh"}


def _tts_speaker() -> str:
    speaker = (os.getenv("SARVAM_TTS_SPEAKER") or "kavitha").strip().lower()
    model = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")
    return "kavitha" if model.endswith("v3") and speaker in _V2_ONLY_SPEAKERS else speaker


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    persona_model: str = field(default_factory=lambda: os.getenv("PAATTI_PERSONA_MODEL", "claude-opus-5-5"))
    extractor_model: str = field(default_factory=lambda: os.getenv("PAATTI_EXTRACTOR_MODEL", "claude-opus-5-5"))
    persona_effort: str = field(default_factory=lambda: os.getenv("PAATTI_PERSONA_EFFORT", "low"))
    llm_timeout_s: float = field(default_factory=lambda: _env_num("PAATTI_LLM_TIMEOUT", "12", float))
    use_llm: bool = field(default_factory=lambda: os.getenv("PAATTI_USE_LLM", "1") != "0")
    provider_override: str = field(default_factory=lambda: os.getenv("PAATTI_LLM_PROVIDER", "").lower())

    # One key or several (GEMINI_API_KEYS=k1,k2) to spread free-tier quota.
    gemini_api_keys: list[str] = field(default_factory=lambda: _list(
        "GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""))
    # Tried in order; a model that 404s is skipped for good, 429/503 cools it down.
    gemini_models: list[str] = field(default_factory=lambda: _list("PAATTI_GEMINI_MODELS", ",".join([
        "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-3.5-flash-lite",
        "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-lite-latest"])))
    # Extraction runs on Lite models first so the persona keeps the main models' quota.
    gemini_extractor_models: list[str] = field(default_factory=lambda: _list("PAATTI_GEMINI_EXTRACTOR_MODELS", ",".join([
        "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest",
        "gemini-3.5-flash", "gemini-flash-latest"])))
    gemini_thinking_level: str = field(default_factory=lambda: os.getenv("PAATTI_GEMINI_THINKING_LEVEL", "minimal"))
    gemini_attempt_timeout_s: float = field(default_factory=lambda: _env_num("PAATTI_GEMINI_ATTEMPT_TIMEOUT", "7", float))
    gemini_rpm: int = field(default_factory=lambda: _env_num("PAATTI_GEMINI_RPM", "8", int))
    gemini_daily_cap: int = field(default_factory=lambda: _env_num("PAATTI_GEMINI_DAILY_CAP", "600", int))
    reply_cache_path: str = field(default_factory=lambda: os.getenv(
        "PAATTI_REPLY_CACHE", str(__import__("pathlib").Path(__file__).parent / "data" / "reply_cache.json")))
    db_path: str = field(default_factory=lambda: os.getenv(
        "PAATTI_DB", str(__import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "paatti.db")))
    reply_cache_write: bool = field(default_factory=lambda: os.getenv("PAATTI_REPLY_CACHE_WRITE", "1") != "0")
    # Demo script mode: rehearsed caller lines get instant, pre-recorded scripted replies.
    demo_mode: bool = field(default_factory=lambda: os.getenv("PAATTI_DEMO_MODE", "1") != "0")
    persona_key: str = field(default_factory=lambda: os.getenv("PAATTI_PERSONA", "paatti"))

    # Telephony (Twilio) and public URL for webhooks / phone demo.
    public_url: str | None = field(default_factory=lambda: (os.getenv("PAATTI_PUBLIC_URL") or "").rstrip("/") or None)
    twilio_account_sid: str | None = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID") or None)
    twilio_auth_token: str | None = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN") or None)
    twilio_number: str | None = field(default_factory=lambda: os.getenv("TWILIO_PHONE_NUMBER") or None)
    operator_token: str | None = field(default_factory=lambda: os.getenv("PAATTI_OPERATOR_TOKEN") or None)
    outbound_allowlist: list[str] = field(default_factory=lambda: _list("PAATTI_OUTBOUND_ALLOWLIST", ""))

    sarvam_api_key: str | None = field(default_factory=lambda: os.getenv("SARVAM_API_KEY") or None)
    sarvam_base_url: str = field(default_factory=lambda: os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai"))
    stt_model: str = field(default_factory=lambda: os.getenv("SARVAM_STT_MODEL", "saaras:v3"))
    stt_mode: str = field(default_factory=lambda: os.getenv("SARVAM_STT_MODE", "translit"))
    tts_model: str = field(default_factory=lambda: os.getenv("SARVAM_TTS_MODEL", "bulbul:v3"))
    tts_speaker: str = field(default_factory=lambda: _tts_speaker())
    # Optional: ask Sarvam for a specific output rate (e.g. 8000 for phone). Unset = provider default.
    tts_sample_rate: int | None = field(
        default_factory=lambda: int(v) if (v := os.getenv("SARVAM_TTS_SAMPLE_RATE", "")) else None
    )
    # Longest pause kept inside a synthesised clip (ms). Shorter = snappier speech; 0 = off.
    # Pitch-preserving speed-up applied after synthesis (1.0 = off). 1.1-1.25 sounds natural.
    tts_speedup: float = field(default_factory=lambda: min(1.6, max(1.0, _env_num("PAATTI_TTS_SPEEDUP", "1.0", float))))
    tts_max_gap_ms: int = field(default_factory=lambda: _env_num("PAATTI_TTS_MAX_GAP_MS", "300", int))
    # Make her sound like she is on a real phone call: 8 kHz narrowband, phone AGC, G.711 grain and a
    # quiet room tone under her voice. Off (0) gives the clean studio voice.
    phone_line: bool = field(default_factory=lambda: os.getenv("PAATTI_PHONE_LINE", "0") == "1")
    line_noise_db: float = field(default_factory=lambda: max(-90.0, min(-30.0, _env_num("PAATTI_LINE_NOISE_DB", "-52", float))))
    audio_cache_dir: str | None = field(default_factory=lambda: os.getenv(
        "PAATTI_AUDIO_CACHE_DIR", str(Path(__file__).parent / "data" / "audio_cache")) or None)
    audio_cache_write: bool = field(default_factory=lambda: os.getenv("PAATTI_AUDIO_CACHE_WRITE", "1") != "0")
    tts_pace: float = field(default_factory=lambda: min(2.0, max(0.5, _env_num("SARVAM_TTS_PACE", "0.9", float))))

    cors_origins: list[str] = field(
        default_factory=lambda: _list("PAATTI_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )
    # Also allow origins matching this regex (e.g. Vercel preview URLs of the dashboard).
    cors_origin_regex: str | None = field(default_factory=lambda: os.getenv("PAATTI_CORS_ORIGIN_REGEX") or None)
    max_turn_chars: int = 2000
    max_turns_per_call: int = field(default_factory=lambda: _env_num("PAATTI_MAX_TURNS", "300", int))
    sarvam_daily_cap: int = field(default_factory=lambda: _env_num("SARVAM_DAILY_CAP", "2000", int))
    # Our own ceiling on Sarvam requests per minute, kept under the account's rate limit so requests
    # wait a moment instead of failing with 429. Live calls may use all of it; warm-up only half.
    sarvam_rpm: int = field(default_factory=lambda: max(2, _env_num("PAATTI_SARVAM_RPM", "20", int)))
    anthropic_daily_cap: int = field(default_factory=lambda: _env_num("PAATTI_ANTHROPIC_DAILY_CAP", "400", int))
    reply_cache_max: int = field(default_factory=lambda: _env_num("PAATTI_REPLY_CACHE_MAX", "2000", int))
    max_audio_bytes: int = 5 * 1024 * 1024
    max_sessions: int = field(default_factory=lambda: _env_num("PAATTI_MAX_SESSIONS", "200", int))
    sessions_per_minute: int = field(default_factory=lambda: _env_num("PAATTI_SESSIONS_PER_MIN", "30", int))

    @property
    def llm_provider(self) -> str:
        """gemini | anthropic | none. Explicit override wins; else Gemini first."""
        if not self.use_llm:
            return "none"
        if self.provider_override in ("gemini", "anthropic", "none"):
            if self.provider_override == "gemini" and not self.gemini_api_keys:
                return "none"
            if self.provider_override == "anthropic" and not self.anthropic_api_key:
                return "none"
            return self.provider_override
        if self.gemini_api_keys:
            return "gemini"
        if self.anthropic_api_key:
            return "anthropic"
        return "none"

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none"

    @property
    def telephony_enabled(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.public_url)

    @property
    def voice_enabled(self) -> bool:
        return bool(self.sarvam_api_key)
