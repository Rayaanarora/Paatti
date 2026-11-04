"""Persona and extractor against a fake Anthropic client (no network)."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from paatti.config import Settings
from paatti.extractor import LLMExtractor, render_transcript
from paatti.persona import Persona
from paatti.schema import Finding, LLMExtraction, Stage
from paatti.session import CallSession, RepeatRegistry


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response

    async def parse(self, **kw):
        return await self.create(**kw)


def fake_client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def text_response(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


def session_with(*scammer_lines):
    s = CallSession(RepeatRegistry())
    for line in scammer_lines:
        s.add_scammer_turn(line)
    return s


@pytest.mark.asyncio
async def test_persona_uses_claude_and_sends_operator_note():
    msgs = FakeMessages(text_response("Aiyo kanna, which bank?"))
    p = Persona(Settings(), client=fake_client(msgs))
    s = session_with("This is CBI. Arrest warrant issued.")
    assert await p.reply(s) == "Aiyo kanna, which bank?"
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert call["messages"][-1]["role"] == "system"
    assert "account number" in call["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_persona_readback_bypasses_llm():
    msgs = FakeMessages(text_response("should not be used"))
    p = Persona(Settings(), client=fake_client(msgs))
    s = session_with("Send to account 3456 7890 1234")
    reply = await p.reply(s)
    assert "3-4-5-6-7-8-9-0-1-2-3-4" in reply
    assert msgs.calls == []


@pytest.mark.asyncio
async def test_persona_falls_back_to_script_on_api_error():
    err = anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))
    p = Persona(Settings(), client=fake_client(FakeMessages(error=err)))
    s = session_with("This is CBI, a case on your Aadhaar")
    reply = await p.reply(s)
    assert reply  # scripted line, call continues


@pytest.mark.asyncio
async def test_persona_refusal_falls_back_to_script():
    p = Persona(Settings(), client=fake_client(FakeMessages(text_response("", stop="refusal"))))
    s = session_with("hello")
    assert await p.reply(s)


def test_scripted_persona_asks_for_missing_identifiers():
    p = Persona(Settings())
    s = session_with("Do not tell anyone, stay on this call")
    assert s.stage == Stage.ISOLATION
    assert "account" in p.scripted_reply(s).lower()


def test_build_messages_alternates_roles():
    p = Persona(Settings())
    s = CallSession(RepeatRegistry())
    s.add_paatti_turn("Hello?")
    s.add_scammer_turn("CBI here")
    s.add_scammer_turn("listen carefully")
    m = p.build_messages(s)
    roles = [x["role"] for x in m]
    assert roles == ["user", "assistant", "user", "system"]


@pytest.mark.asyncio
async def test_llm_extractor_merges_valid_and_rejects_hallucinated():
    s = session_with("Pay to account number double five six seven eight nine one two three four")
    parsed = LLMExtraction(
        stage="extraction",
        findings=[
            Finding(field="account_number", value="556789123400", quote="this quote was never said", turn_index=0),
            Finding(field="upi_id", value="fake@ybl", quote="Pay to account", turn_index=0),
            Finding(field="ifsc", value="NOTANIFSC", quote="Pay to account", turn_index=0),
        ],
    )
    resp = SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)
    msgs = FakeMessages(resp)
    ex = LLMExtractor(Settings(), client=fake_client(msgs))
    accepted = await ex.update(s)
    # Only the UPI finding has a verbatim quote and a valid format.
    assert accepted == 1
    assert s.intel.upi_id.value == "fake@ybl"
    assert s.intel.upi_id.source == "llm"
    assert s.stage == Stage.EXTRACTION
    assert msgs.calls[0]["output_format"] is LLMExtraction


@pytest.mark.asyncio
async def test_llm_extractor_marks_agreement_with_rules():
    s = session_with("UPI is cbi.case@okaxis")
    parsed = LLMExtraction(
        stage="none",
        findings=[Finding(field="upi_id", value="cbi.case@okaxis", quote="cbi.case@okaxis", turn_index=0)],
    )
    ex = LLMExtractor(Settings(), client=fake_client(FakeMessages(SimpleNamespace(stop_reason="end_turn", parsed_output=parsed))))
    await ex.update(s)
    assert s.intel.upi_id.source == "rules+llm"
    assert s.intel.upi_id.confidence == 0.85


@pytest.mark.asyncio
async def test_llm_extractor_handles_errors_and_disabled():
    s = session_with("hello")
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    assert await LLMExtractor(Settings(), client=fake_client(FakeMessages(error=err))).update(s) == 0
    disabled = LLMExtractor(Settings(anthropic_api_key=None))
    assert not disabled.enabled
    assert await disabled.extract(s) is None


def test_render_transcript_labels_roles():
    s = session_with("CBI here")
    s.add_paatti_turn("Aiyo")
    assert render_transcript(s) == "[0] SCAMMER: CBI here\n[1] PAATTI: Aiyo"
