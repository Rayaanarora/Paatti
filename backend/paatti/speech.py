"""Low-latency speech output: split a reply into sentences and stream them.

Instead of synthesising the whole reply and sending one large audio file, the
reply is split into short chunks (the first one deliberately short), every
chunk is sent to text-to-speech at once, and chunks are yielded in order as
soon as each is ready. The caller hears the first sentence after roughly one
short TTS round-trip while the rest is still being generated.
"""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator, Protocol

# Split after sentence punctuation followed by whitespace ("4.5" and "a.b@ybl" stay intact).
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+")

FIRST_MIN = 12   # first chunk: as short as possible, but a real phrase
MIN_LEN = 40     # later chunks: big enough to sound natural
MAX_LEN = 160    # never send a huge chunk
MAX_PARALLEL = 4


class TTS(Protocol):
    async def synthesize(self, text: str, language_code: str = ..., speaker: str | None = ...) -> bytes: ...

    def cached(self, text: str, language_code: str = ..., speaker: str | None = ...) -> bytes | None: ...


def _cap(piece: str) -> list[str]:
    """Break an over-long sentence at clause boundaries, then at word boundaries."""
    if len(piece) <= MAX_LEN:
        return [piece]
    out: list[str] = []
    for clause in _CLAUSE.split(piece):
        while len(clause) > MAX_LEN:
            cut = clause.rfind(" ", 0, MAX_LEN)
            cut = cut if cut > 0 else MAX_LEN
            out.append(clause[:cut].strip())
            clause = clause[cut:].strip()
        if clause:
            out.append(clause)
    return out


def split_for_tts(text: str) -> list[str]:
    pieces = [p for s in _SENTENCE.split(text.strip()) if s.strip() for p in _cap(s.strip())]
    chunks: list[str] = []
    buf = ""
    for p in pieces:
        buf = f"{buf} {p}".strip() if buf else p
        target = FIRST_MIN if not chunks else MIN_LEN
        if len(buf) >= target:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks and len(buf) < MIN_LEN // 2 and len(chunks[-1]) + len(buf) < MAX_LEN:
            chunks[-1] = f"{chunks[-1]} {buf}"  # don't end on a tiny clipped chunk
        else:
            chunks.append(buf)
    return chunks


async def synth_stream(tts: TTS, text: str, language_code: str, speaker: str | None
                       ) -> AsyncIterator[tuple[str, bytes]]:
    """Yield (chunk_text, wav_bytes) in order, synthesising all chunks concurrently."""
    lookup = getattr(tts, "cached", None)
    whole = lookup(text, language_code, speaker) if lookup else None
    if whole is not None:  # pre-generated line: play it as-is, zero latency
        yield text, whole
        return
    chunks = split_for_tts(text)
    if not chunks:
        return
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(chunk: str) -> bytes:
        async with sem:
            return await tts.synthesize(chunk, language_code=language_code, speaker=speaker)

    tasks = [asyncio.create_task(one(c)) for c in chunks]
    try:
        for chunk, task in zip(chunks, tasks):
            yield chunk, await task
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


# ---- natural pacing ------------------------------------------------------------

_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)\s*")
_SPACES = re.compile(r"\s{2,}")


def normalize_for_tts(text: str) -> str:
    """Make written dialogue sound like speech: TTS turns '...' into long silences,
    so ellipses become a short comma pause. Keeps the words exactly the same."""
    t = _ELLIPSIS.sub(", ", text.strip())
    t = re.sub(r",\s*([,.?!])", r"\1", t)          # ", ?" -> "?"
    t = re.sub(r"^[,\s]+|[,\s]+$", "", t)           # no leading/trailing comma
    return _SPACES.sub(" ", t)


# ---- streaming: speak sentences as they arrive -----------------------------------

class SentenceStreamer:
    """Feed streamed LLM text; get back complete sentences as soon as they're speakable."""

    _BOUNDARY = re.compile(r"[.!?…](?:\s|$)")

    def __init__(self) -> None:
        self.buf = ""
        self.emitted = 0

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            target = FIRST_MIN if self.emitted == 0 and not out else MIN_LEN // 2
            cut = None
            for m in self._BOUNDARY.finditer(self.buf):
                if m.end() >= target and m.end() < len(self.buf):  # need text after it (not mid-"4.5")
                    cut = m.end()
                    break
            if cut is None:
                break
            sentence, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if sentence:
                out.append(sentence)
        self.emitted += len(out)
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


async def speak_stream(sentences: AsyncIterator[str], tts: TTS, language_code: str, speaker: str | None,
                       send) -> int:
    """Synthesise sentences the moment they arrive (in parallel) and `await send(chunk, wav)`
    strictly in order. Returns how many chunks were sent."""
    order: asyncio.Queue = asyncio.Queue()
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(chunk: str) -> bytes:
        async with sem:
            return await tts.synthesize(chunk, language_code=language_code, speaker=speaker)

    async def producer() -> None:
        try:
            async for sentence in sentences:
                for chunk in split_for_tts(sentence) or []:
                    await order.put((chunk, asyncio.create_task(one(chunk))))
        finally:
            await order.put(None)

    prod = asyncio.create_task(producer())
    sent = 0
    try:
        while True:
            item = await order.get()
            if item is None:
                break
            chunk, task = item
            await send(chunk, await task)
            sent += 1
        await prod
    finally:
        prod.cancel()
        while not order.empty():
            item = order.get_nowait()
            if item:
                item[1].cancel()
    return sent
