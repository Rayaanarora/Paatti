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


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    persona_model: str = field(default_factory=lambda: os.getenv("PAATTI_PERSONA_MODEL", "claude-opus-5-5"))
    extractor_model: str = field(default_factory=lambda: os.getenv("PAATTI_EXTRACTOR_MODEL", "claude-opus-5-5"))
    persona_effort: str = field(default_factory=lambda: os.getenv("PAATTI_PERSONA_EFFORT", "low"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("PAATTI_LLM_TIMEOUT", "12")))
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
    gemini_attempt_timeout_s: float = field(default_factory=lambda: float(os.getenv("PAATTI_GEMINI_ATTEMPT_TIMEOUT", "7")))
    gemini_rpm: int = field(default_factory=lambda: int(os.getenv("PAATTI_GEMINI_RPM", "8")))
    gemini_daily_cap: int = field(default_factory=lambda: int(os.getenv("PAATTI_GEMINI_DAILY_CAP", "600")))
    reply_cache_path: str = field(default_factory=lambda: os.getenv(
        "PAATTI_REPLY_CACHE", str(__import__("pathlib").Path(__file__).parent / "data" / "reply_cache.json")))
    db_path: str = field(default_factory=lambda: os.getenv(
        "PAATTI_DB", str(__import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "paatti.db")))
    reply_cache_write: bool = field(default_factory=lambda: os.getenv("PAATTI_REPLY_CACHE_WRITE", "1") != "0")
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
    tts_speaker: str = field(default_factory=lambda: os.getenv("SARVAM_TTS_SPEAKER", "kavitha"))
    # Optional: ask Sarvam for a specific output rate (e.g. 8000 for phone). Unset = provider default.
    tts_sample_rate: int | None = field(
        default_factory=lambda: int(v) if (v := os.getenv("SARVAM_TTS_SAMPLE_RATE", "")) else None
    )
    audio_cache_dir: str | None = field(default_factory=lambda: os.getenv(
        "PAATTI_AUDIO_CACHE_DIR", str(Path(__file__).parent / "data" / "audio_cache")) or None)
    audio_cache_write: bool = field(default_factory=lambda: os.getenv("PAATTI_AUDIO_CACHE_WRITE", "1") != "0")
    tts_pace: float = field(default_factory=lambda: float(os.getenv("SARVAM_TTS_PACE", "1.2")))

    cors_origins: list[str] = field(
        default_factory=lambda: _list("PAATTI_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )
    # Also allow origins matching this regex (e.g. Vercel preview URLs of the dashboard).
    cors_origin_regex: str | None = field(default_factory=lambda: os.getenv("PAATTI_CORS_ORIGIN_REGEX") or None)
    max_turn_chars: int = 2000
    max_turns_per_call: int = field(default_factory=lambda: int(os.getenv("PAATTI_MAX_TURNS", "300")))
    sarvam_daily_cap: int = field(default_factory=lambda: int(os.getenv("SARVAM_DAILY_CAP", "2000")))
    anthropic_daily_cap: int = field(default_factory=lambda: int(os.getenv("PAATTI_ANTHROPIC_DAILY_CAP", "400")))
    reply_cache_max: int = field(default_factory=lambda: int(os.getenv("PAATTI_REPLY_CACHE_MAX", "2000")))
    max_audio_bytes: int = 5 * 1024 * 1024
    max_sessions: int = field(default_factory=lambda: int(os.getenv("PAATTI_MAX_SESSIONS", "200")))
    sessions_per_minute: int = field(default_factory=lambda: int(os.getenv("PAATTI_SESSIONS_PER_MIN", "30")))

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
