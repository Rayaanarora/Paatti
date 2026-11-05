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


def test_normalize_for_tts_removes_long_pauses_keeps_words():
    from paatti.speech import normalize_for_tts

    assert normalize_for_tts("Aiyo, konjam porunga kanna... pen edutten.") == "Aiyo, konjam porunga kanna, pen edutten."
    assert normalize_for_tts("Enna...?") == "Enna?"
    assert normalize_for_tts("...Hello…  kanna") == "Hello, kanna"
    assert normalize_for_tts("Rs 4.5 lakh") == "Rs 4.5 lakh"


def test_sentence_streamer_emits_as_soon_as_speakable():
    from paatti.speech import SentenceStreamer

    st = SentenceStreamer()
    assert st.feed("Aiyo kanna, enna") == []
    assert st.feed(" solreenga? Case") == ["Aiyo kanna, enna solreenga?"]
    assert st.feed("-aa? Rs 4.5 lakh-aa") == []          # "4.5" is not a boundary; "Case-aa?" too short alone
    assert st.feed("? Naan oru teacher. ") == ["Case-aa? Rs 4.5 lakh-aa?"]
    assert st.flush() == ["Naan oru teacher."]


async def test_speak_stream_speaks_first_sentence_before_the_rest_exists():
    from paatti.speech import speak_stream

    events = []

    async def sentences():
        yield "Aiyo kanna, enna solreenga?"
        events.append("model still writing")
        await asyncio.sleep(0.1)
        yield "Naan oru retired teacher, enakku onnum theriyaadhu."

    async def send(chunk, wav):
        events.append(("sent", chunk))

    n = await speak_stream(sentences(), FakeTTS({}), "ta-IN", None, send)
    assert n == 2
    assert events.index(("sent", "Aiyo kanna, enna solreenga?")) < events.index("model still writing") + 2
    assert events[-1] == ("sent", "Naan oru retired teacher, enakku onnum theriyaadhu.")
