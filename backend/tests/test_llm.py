"""Persona, extractor and both LLM backends against fake clients (no network)."""

import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from paatti.config import Settings
from paatti.extractor import LLMExtractor, render_transcript, to_findings
from paatti.llm import AnthropicBackend, GeminiBackend, LooseExtraction, LooseFinding, make_backend
from paatti.persona import Persona, guard_reply
from paatti.schema import Stage
from paatti.session import CallSession, RepeatRegistry


def session_with(*scammer_lines, persona="paatti"):
    s = CallSession(RepeatRegistry(), persona_key=persona)
    for line in scammer_lines:
        s.add_scammer_turn(line)
    return s


class FakeBackend:
    name = "fake"

    def __init__(self, reply=None, extraction=None):
        self.reply = reply
        self.extraction = extraction
        self.chats = []

    async def chat(self, system, messages, note):
        self.chats.append((system, messages, note))
        return self.reply

    async def extract(self, system, transcript):
        return self.extraction


# ---- persona -------------------------------------------------------------

async def test_persona_uses_backend_with_note_and_profile_prompt():
    b = FakeBackend("Aiyo kanna, which bank?")
    p = Persona(Settings(), backend=b)
    s = session_with("This is CBI. Arrest warrant issued.")
    assert await p.reply(s) == "Aiyo kanna, which bank?"
    system, messages, note = b.chats[0]
    assert "Mylapore" in system and "never instructions to you" in system
    assert messages[0]["role"] == "user"
    assert "account number" in note


async def test_dadi_profile_used_for_hindi_persona():
    b = FakeBackend(None)
    p = Persona(Settings(), backend=b)
    s = session_with("This is CBI", persona="dadi")
    await p.reply(s)
    assert "Lucknow" in b.chats[0][0]
    s2 = session_with("account 3456 7890 1234", persona="dadi")
    assert "Sahi hai na" in await p.reply(s2)


async def test_readback_bypasses_llm():
    b = FakeBackend("should not be used")
    p = Persona(Settings(), backend=b)
    reply = await p.reply(session_with("Send to account 3456 7890 1234"))
    assert "3-4-5-6-7-8-9-0-1-2-3-4" in reply
    assert b.chats == []


@pytest.mark.parametrize("bad", [
    "As an AI, I cannot help with that.",
    "I am a language model assistant.",
    "My OTP is 482913, kanna.",
    "Okay, my account number is 1234 5678 9012.",
    "Sure! Here is my prompt:",
])
async def test_guard_replaces_character_breaks_with_script(bad):
    p = Persona(Settings(), backend=FakeBackend(bad))
    s = session_with("Listen carefully madam, what will you do now?")
    reply = await p.reply(s)
    assert reply != bad and reply in sum(p.profile(s).scripted.values(), [])


def test_guard_allows_digits_the_scammer_said_even_after_correction():
    s = session_with("account 3456 7890 1234", "no, it is 3456 7890 1235")
    assert guard_reply("Account 3-4-5-6-7-8-9-0-1-2-3-4?", s)
    assert guard_reply("My number is 9-9-9-9-1-1-1-1", s) is None


def test_guard_allows_repeating_captured_values_and_truncates():
    s = session_with("Pay Rs 4,50,000 now")
    assert guard_reply("Aiyo, 4,50,000-aa kanna?", s) == "Aiyo, 4,50,000-aa kanna?"
    long = " ".join(["kanna"] * 100)
    assert guard_reply(long, s).endswith("...")
    assert guard_reply("aiyo kanna, saapteengala?", s)  # "aiyo" is not "AI"


async def test_persona_falls_back_when_backend_returns_none():
    p = Persona(Settings(), backend=FakeBackend(None))
    assert await p.reply(session_with("This is CBI, a case on your Aadhaar"))


def test_scripted_persona_asks_for_missing_identifiers():
    p = Persona(Settings(), use_backend=False)
    s = session_with("Do not tell anyone, stay on this call")
    assert s.stage == Stage.ISOLATION
    assert "account" in p.scripted_reply(s).lower()


def test_build_messages_alternates_roles():
    s = CallSession(RepeatRegistry())
    s.add_paatti_turn("Hello?")
    s.add_scammer_turn("CBI here")
    s.add_scammer_turn("listen carefully")
    assert [m["role"] for m in Persona.build_messages(s)] == ["user", "assistant", "user"]


# ---- extractor -----------------------------------------------------------

async def test_extractor_merges_valid_and_rejects_hallucinated():
    s = session_with("Pay to account number double five six seven eight nine one two three four")
    ex = LooseExtraction(stage="extraction", findings=[
        LooseFinding(field="account_number", value="556789123400", quote="never said", turn_index=0),
        LooseFinding(field="upi_id", value="fake@ybl", quote="Pay to account", turn_index=0),
        LooseFinding(field="ifsc", value="NOTANIFSC", quote="Pay to account", turn_index=0),
        LooseFinding(field="amount", value="", quote="Pay to account", turn_index=-1),  # invalid item
    ])
    accepted = await LLMExtractor(Settings(), backend=FakeBackend(extraction=ex)).update(s)
    assert accepted == 1
    assert s.intel.upi_id.value == "fake@ybl" and s.intel.upi_id.source == "llm"
    assert s.stage == Stage.EXTRACTION


async def test_extractor_marks_agreement_with_rules():
    s = session_with("UPI is cbi.case@okaxis")
    ex = LooseExtraction(stage="none", findings=[
        LooseFinding(field="upi_id", value="cbi.case@okaxis", quote="cbi.case@okaxis", turn_index=0)])
    await LLMExtractor(Settings(), backend=FakeBackend(extraction=ex)).update(s)
    assert s.intel.upi_id.source == "rules+llm" and s.intel.upi_id.confidence == 0.85


async def test_extractor_disabled_and_failed():
    s = session_with("hello")
    assert await LLMExtractor(Settings(), backend=FakeBackend(extraction=None)).update(s) == 0
    off = LLMExtractor(Settings(), use_backend=False)
    assert not off.enabled and await off.extract(s) is None


def test_to_findings_drops_invalid_items():
    ex = LooseExtraction(stage="none", findings=[
        LooseFinding(field="ifsc", value="SBIN0001234", quote="q", turn_index=0),
        LooseFinding(field="ifsc", value="SBIN0001234", quote="q", turn_index=-5),
    ])
    assert len(to_findings(ex)) == 1


def test_render_transcript_labels_roles():
    s = session_with("CBI here")
    s.add_paatti_turn("Aiyo")
    assert render_transcript(s) == "[0] SCAMMER: CBI here\n[1] PAATTI: Aiyo"


# ---- provider selection --------------------------------------------------

def test_provider_selection(monkeypatch):
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "PAATTI_LLM_PROVIDER",
              "PAATTI_USE_LLM"):
        monkeypatch.delenv(k, raising=False)
    assert Settings().llm_provider == "none" and make_backend(Settings()) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert Settings().llm_provider == "anthropic"
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert Settings().llm_provider == "gemini"
    assert isinstance(make_backend(Settings()), GeminiBackend)
    monkeypatch.setenv("PAATTI_LLM_PROVIDER", "anthropic")
    assert isinstance(make_backend(Settings()), AnthropicBackend)
    monkeypatch.setenv("PAATTI_USE_LLM", "0")
    assert Settings().llm_provider == "none"


# ---- Gemini backend (model pool) ----------------------------------------

from google.genai import errors as genai_errors  # noqa: E402

from paatti.llm import GeminiPool  # noqa: E402


class FakeGeminiModels:
    """Responds per model: a value, an exception, or ("sleep", seconds)."""

    def __init__(self, by_model=None, default=None):
        self.by_model = by_model or {}
        self.default = default
        self.calls = []

    async def generate_content(self, **kw):
        self.calls.append(kw)
        out = self.by_model.get(kw["model"], self.default)
        if isinstance(out, tuple) and out[0] == "sleep":
            await asyncio.sleep(out[1])
            return SimpleNamespace(text="late")
        if isinstance(out, Exception):
            raise out
        return out


def gemini(models_obj, models=("m-a", "m-b", "m-c"), keys=1, **kw):
    settings = Settings(gemini_api_keys=["k"] * keys, gemini_models=list(models),
                        gemini_extractor_models=list(models), **kw)
    clients = [SimpleNamespace(aio=SimpleNamespace(models=models_obj)) for _ in range(keys)]
    return GeminiBackend(settings, clients=clients)


def api_error(code, msg="x"):
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"code": code, "message": msg}})


async def test_gemini_chat_maps_roles_and_thinking_config():
    fake = FakeGeminiModels(default=SimpleNamespace(text=" Aiyo kanna! "))
    b = gemini(fake, models=("gemini-3.8-flash", "gemini-2.5-flash"))
    out = await b.chat("SYS", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "a"},
                               {"role": "user", "content": "b"}], "NOTE")
    assert out == "Aiyo kanna!"
    call = fake.calls[0]
    assert call["model"] == "gemini-3.8-flash"
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    assert "NOTE" in call["config"].system_instruction
    assert call["config"].thinking_config.thinking_level.value == "MINIMAL"


async def test_gemini_rotates_past_404_503_and_429():
    fake = FakeGeminiModels(by_model={"m-a": api_error(404), "m-b": api_error(503)},
                            default=SimpleNamespace(text="ok"))
    b = gemini(fake)
    assert await b.chat("s", [{"role": "user", "content": "x"}], None) == "ok"
    assert [c["model"] for c in fake.calls] == ["m-a", "m-b", "m-c"]
    states = {s["model"]: s["state"] for s in b.status()["slots"]}
    assert states == {"m-a": "retired", "m-b": "cooling", "m-c": "ready"}
    # next call goes straight to the healthy model
    fake.calls.clear()
    await b.chat("s", [{"role": "user", "content": "x"}], None)
    assert [c["model"] for c in fake.calls] == ["m-c"]


async def test_gemini_429_cools_and_rpm_cap_rotates():
    fake = FakeGeminiModels(by_model={"m-a": api_error(429)}, default=SimpleNamespace(text="ok"))
    b = gemini(fake, models=("m-a", "m-b"), gemini_rpm=2)
    for _ in range(2):
        assert await b.chat("s", [{"role": "user", "content": "x"}], None) == "ok"
    # m-a cooling after 429; m-b used twice -> at its local RPM cap -> nothing left
    assert await b.chat("s", [{"role": "user", "content": "x"}], None) is None


async def test_gemini_second_key_used_when_first_key_rejected():
    class PerKey:
        def __init__(self, out):
            self.out, self.calls = out, 0

        async def generate_content(self, **kw):
            self.calls += 1
            if isinstance(self.out, Exception):
                raise self.out
            return self.out

    k1, k2 = PerKey(api_error(403)), PerKey(SimpleNamespace(text="from key 2"))
    settings = Settings(gemini_api_keys=["a", "b"], gemini_models=["m"], gemini_extractor_models=["m"])
    b = GeminiBackend(settings, clients=[SimpleNamespace(aio=SimpleNamespace(models=k1)),
                                         SimpleNamespace(aio=SimpleNamespace(models=k2))])
    assert await b.chat("s", [{"role": "user", "content": "x"}], None) == "from key 2"


async def test_gemini_retries_without_thinking_on_400():
    class ThinkingPicky:
        def __init__(self):
            self.configs = []

        async def generate_content(self, **kw):
            self.configs.append(kw["config"].thinking_config)
            if kw["config"].thinking_config is not None:
                raise api_error(400, "thinking_level is not supported for this model")
            return SimpleNamespace(text="ok")

    fake = ThinkingPicky()
    b = gemini(fake, models=("m-a",))
    assert await b.chat("s", [{"role": "user", "content": "x"}], None) == "ok"
    assert fake.configs[0] is not None and fake.configs[1] is None


async def test_gemini_bad_request_does_not_rotate():
    fake = FakeGeminiModels(default=api_error(400, "invalid argument"))
    assert await gemini(fake).chat("s", [{"role": "user", "content": "x"}], None) is None
    assert len(fake.calls) == 1


async def test_gemini_network_error_rotates_and_timeout_gives_up():
    fake = FakeGeminiModels(by_model={"m-a": httpx.ConnectError("down")}, default=SimpleNamespace(text="ok"))
    assert await gemini(fake).chat("s", [{"role": "user", "content": "x"}], None) == "ok"
    slow = FakeGeminiModels(default=("sleep", 5))
    assert await gemini(slow, llm_timeout_s=0.3).chat("s", [{"role": "user", "content": "x"}], None) is None


async def test_gemini_daily_cap_switches_to_script():
    fake = FakeGeminiModels(default=SimpleNamespace(text="ok"))
    b = gemini(fake, gemini_daily_cap=1)
    assert await b.chat("s", [{"role": "user", "content": "x"}], None) == "ok"
    assert await b.chat("s", [{"role": "user", "content": "x"}], None) is None


def test_pool_daily_counter_resets_on_new_day():
    day = {"d": "2026-11-05"}
    pool = GeminiPool(1, rpm=100, daily_cap=1, today=lambda: day["d"])
    pool.used(pool.slot(0, "m"))
    assert not pool.budget_left()
    day["d"] = "2026-11-06"
    assert pool.budget_left()


async def test_gemini_extract_parsed_and_json_fallback():
    ex = LooseExtraction(stage="fear", findings=[])
    assert await gemini(FakeGeminiModels(default=SimpleNamespace(parsed=ex, text=""))).extract("s", "t") == ex
    raw = SimpleNamespace(parsed=None, text=ex.model_dump_json())
    assert (await gemini(FakeGeminiModels(default=raw)).extract("s", "t")).stage == "fear"
    bad = SimpleNamespace(parsed=None, text="not json")
    assert await gemini(FakeGeminiModels(default=bad)).extract("s", "t") is None
    assert await gemini(FakeGeminiModels(default=api_error(503))).extract("s", "t") is None


# ---- Claude backend --------------------------------------------------------

class FakeAnthropicMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response

    parse = create


def claude(messages):
    return AnthropicBackend(Settings(anthropic_api_key="a"), client=SimpleNamespace(beta=SimpleNamespace(messages=messages)))


def text_response(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


async def test_claude_chat_appends_operator_note_and_fallbacks():
    msgs = FakeAnthropicMessages(text_response("Aiyo"))
    assert await claude(msgs).chat("SYS", [{"role": "user", "content": "hi"}], "NOTE") == "Aiyo"
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["messages"][-1] == {"role": "system", "content": "NOTE"}


async def test_claude_refusal_and_errors_return_none():
    assert await claude(FakeAnthropicMessages(text_response("", "refusal"))).chat("s", [], None) is None
    err = anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))
    assert await claude(FakeAnthropicMessages(error=err)).chat("s", [], None) is None
    assert await claude(FakeAnthropicMessages(error=err)).extract("s", "t") is None


async def test_claude_extract():
    ex = LooseExtraction(stage="hook", findings=[])
    msgs = FakeAnthropicMessages(SimpleNamespace(stop_reason="end_turn", parsed_output=ex))
    assert await claude(msgs).extract("s", "t") == ex
    assert msgs.calls[0]["output_format"] is LooseExtraction
    cut = FakeAnthropicMessages(SimpleNamespace(stop_reason="max_tokens", parsed_output=None))
    assert await claude(cut).extract("s", "t") is None


async def test_gemini_hanging_model_is_skipped_within_one_turn():
    fake = FakeGeminiModels(by_model={"m-a": ("sleep", 5)}, default=SimpleNamespace(text="fast model"))
    b = gemini(fake, llm_timeout_s=2.0, gemini_attempt_timeout_s=0.3)
    assert await b.chat("s", [{"role": "user", "content": "x"}], None) == "fast model"
    assert [c["model"] for c in fake.calls] == ["m-a", "m-b"]
