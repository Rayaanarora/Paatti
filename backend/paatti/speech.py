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
