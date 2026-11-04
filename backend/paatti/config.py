"""Runtime configuration from environment variables. No secrets in code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    persona_model: str = field(default_factory=lambda: os.getenv("PAATTI_PERSONA_MODEL", "claude-opus-5-5"))
    extractor_model: str = field(default_factory=lambda: os.getenv("PAATTI_EXTRACTOR_MODEL", "claude-opus-5-5"))
    persona_effort: str = field(default_factory=lambda: os.getenv("PAATTI_PERSONA_EFFORT", "low"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("PAATTI_LLM_TIMEOUT", "8")))
    use_llm: bool = field(default_factory=lambda: os.getenv("PAATTI_USE_LLM", "1") != "0")

    sarvam_api_key: str | None = field(default_factory=lambda: os.getenv("SARVAM_API_KEY") or None)
    sarvam_base_url: str = field(default_factory=lambda: os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai"))
    stt_model: str = field(default_factory=lambda: os.getenv("SARVAM_STT_MODEL", "saaras:v3"))
    tts_model: str = field(default_factory=lambda: os.getenv("SARVAM_TTS_MODEL", "bulbul:v3"))
    tts_speaker: str = field(default_factory=lambda: os.getenv("SARVAM_TTS_SPEAKER", "anushka"))
    tts_pace: float = field(default_factory=lambda: float(os.getenv("SARVAM_TTS_PACE", "0.8")))

    cors_origins: list[str] = field(
        default_factory=lambda: _list("PAATTI_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )
    max_turn_chars: int = 2000
    max_audio_bytes: int = 5 * 1024 * 1024
    max_sessions: int = field(default_factory=lambda: int(os.getenv("PAATTI_MAX_SESSIONS", "200")))
    sessions_per_minute: int = field(default_factory=lambda: int(os.getenv("PAATTI_SESSIONS_PER_MIN", "30")))

    @property
    def llm_enabled(self) -> bool:
        return self.use_llm and bool(self.anthropic_api_key)

    @property
    def voice_enabled(self) -> bool:
        return bool(self.sarvam_api_key)
