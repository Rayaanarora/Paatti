import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Every test gets its own database and reply cache; no API keys leak in."""
    monkeypatch.setenv("PAATTI_DB", str(tmp_path / "paatti.db"))
    monkeypatch.setenv("PAATTI_REPLY_CACHE", str(tmp_path / "reply_cache.json"))
    monkeypatch.setenv("PAATTI_AUDIO_CACHE_DIR", str(tmp_path / "audio_cache"))
    for key in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "SARVAM_API_KEY",
                "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "PAATTI_LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
