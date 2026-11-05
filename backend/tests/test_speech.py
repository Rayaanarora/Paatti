import asyncio
import time

from paatti.audio import wav_from_pcm
from paatti.speech import split_for_tts, synth_stream


def test_short_text_is_one_chunk():
    assert split_for_tts("Sariya kanna?") == ["Sariya kanna?"]
    assert split_for_tts("  ") == []


def test_first_chunk_is_short_for_fast_start():
    text = "Aiyo kanna, enna solreenga? Case-aa? Enakku onnum theriyaadhe... konjam pothuva sollunga."
    chunks = split_for_tts(text)
    assert len(chunks) >= 2
    assert chunks[0] == "Aiyo kanna, enna solreenga?"
    assert " ".join(chunks) == text  # nothing lost or reordered


def test_tiny_tail_merges_into_previous_chunk():
    text = "Seri kanna, pay panren. Pen edukkaren, account number oru oru digit-a mella sollunga. Sari?"
    chunks = split_for_tts(text)
    assert chunks[-1].endswith("Sari?") and len(chunks[-1]) > len("Sari?")


def test_decimals_and_ellipses():
    chunks = split_for_tts("Rs 4.5 lakh-aa kanna? Aiyo... avvalo panam illa, konjam porunga, naan bank-ku pogaren.")
    assert any("4.5 lakh" in c for c in chunks)  # no split inside numbers


def test_long_sentence_is_capped():
    text = ("kanna " * 60).strip() + "."
    assert all(len(c) <= 170 for c in split_for_tts(text))


class FakeTTS:
    def __init__(self, delays):
        self.delays = delays
        self.started = []

    async def synthesize(self, text, language_code="ta-IN", speaker=None):
        self.started.append((time.monotonic(), text))
        await asyncio.sleep(self.delays.get(text, 0.05))
        return wav_from_pcm(text.encode().ljust(16, b"\0"), 16000)

    def cached(self, text, language_code="ta-IN", speaker=None):
        return None


async def test_stream_keeps_order_and_synthesises_in_parallel():
    text = "First sentence here kanna. Second one is a bit longer, kanna, really. Third and final sentence now."
    chunks = split_for_tts(text)
    # Make the first chunk the slowest: order must still be preserved.
    tts = FakeTTS({chunks[0]: 0.2})
    t0 = time.monotonic()
    out = [c async for c in synth_stream(tts, text, "ta-IN", "kavitha")]
    assert [c[0] for c in out] == chunks
    # All chunks started almost at once (parallel), not one after another.
    assert max(t for t, _ in tts.started) - t0 < 0.1
    assert time.monotonic() - t0 < 0.35


async def test_stream_uses_whole_cached_line_when_available():
    class Cached(FakeTTS):
        def cached(self, text, language_code="ta-IN", speaker=None):
            return b"WHOLE" if text.startswith("Aiyo") else None

    tts = Cached({})
    out = [c async for c in synth_stream(tts, "Aiyo, oru nimisham kanna. Porunga, pen edukkaren.", "ta-IN", None)]
    assert out == [("Aiyo, oru nimisham kanna. Porunga, pen edukkaren.", b"WHOLE")]
    assert tts.started == []
