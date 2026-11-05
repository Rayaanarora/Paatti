"""Streaming voice pipeline shared by the Twilio phone line and the browser phone.

caller audio -> VAD -> Sarvam STT -> persona -> Sarvam TTS -> audio back

To hide model latency, a short pre-synthesised filler ("Aiyo, oru nimisham
kanna...") plays if the reply isn't ready quickly, so the pause sounds like an
old lady fumbling rather than a slow machine.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import OrderedDict
from typing import Awaitable, Callable

from .audio import VAD, pcm_from_wav, resample, wav_from_pcm
from .personas import get_profile
from .sarvam import SarvamClient, SarvamError
from .service import CallService
from .session import CallSession

log = logging.getLogger(__name__)

STT_RATE = 16000
FILLER_AFTER_S = 0.6
SECOND_FILLER_AFTER_S = 3.0


class TTSCache:
    """Small LRU of synthesised PCM keyed by (language, speaker, text, rate)."""

    def __init__(self, size: int = 128):
        self.size = size
        self._data: OrderedDict[tuple, bytes] = OrderedDict()

    def get(self, key: tuple) -> bytes | None:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key: tuple, pcm: bytes) -> None:
        self._data[key] = pcm
        self._data.move_to_end(key)
        while len(self._data) > self.size:
            self._data.popitem(last=False)


TTS_CACHE = TTSCache()


class VoiceCall:
    def __init__(
        self,
        service: CallService,
        session: CallSession,
        sarvam: SarvamClient,
        *,
        in_rate: int,
        out_rate: int,
        send_audio: Callable[[bytes], Awaitable[None]],
        clear_audio: Callable[[], Awaitable[None]],
        barge_in: bool = True,
        echo_guard: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.service = service
        self.session = session
        self.sarvam = sarvam
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.send_audio = send_audio
        self.clear_audio = clear_audio
        self.barge_in = barge_in
        self.echo_guard = echo_guard
        self.clock = clock
        self.profile = get_profile(session.persona_key)
        self.vad = VAD(rate=in_rate)
        self.play_until = 0.0
        # Bounded: if the caller floods audio faster than we can answer, drop extra utterances
        # instead of queueing unlimited paid STT/LLM/TTS work.
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
        self._worker: asyncio.Task | None = None
        self._fillers = itertools.cycle(self.profile.fillers)
        self._unclear = itertools.cycle(self.profile.unclear or self.profile.fillers)
        self._empty_streak = 0

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None

    async def warm(self) -> None:
        """Pre-synthesise greeting and fillers so they play instantly."""
        lines = [self.profile.greeting, *self.profile.fillers[:3], *(self.profile.unclear[:1])]
        await asyncio.gather(*(self._tts(t, cache=True) for t in lines), return_exceptions=True)

    async def greet(self) -> None:
        self.service.say(self.session, self.profile.greeting)
        await self.play(self.profile.greeting, cache=True)

    # ---- audio in --------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self.clock() < self.play_until

    async def feed(self, pcm: bytes) -> None:
        if self.echo_guard and self.clock() < self.play_until + 0.25:
            return  # speakerphone: ignore our own voice coming back
        for kind, data in self.vad.feed(pcm):
            if kind == "speech_start" and self.barge_in and self.playing:
                self.play_until = 0.0
                await self.clear_audio()
            elif kind == "utterance" and data:
                try:
                    self._queue.put_nowait(data)
                except asyncio.QueueFull:
                    log.warning("dropping utterance: caller is ahead of the pipeline")

    # ---- turn processing -------------------------------------------------
    async def _run(self) -> None:
        while True:
            pcm = await self._queue.get()
            try:
                await self.handle_utterance(pcm)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("voice turn failed")

    async def handle_utterance(self, pcm: bytes) -> None:
        wav = wav_from_pcm(resample(pcm, self.in_rate, STT_RATE), STT_RATE)
        try:
            text = await self.sarvam.transcribe(wav, filename="turn.wav", mime="audio/wav")
        except SarvamError as e:
            self.service.hub.publish(self.session.id, {"type": "notice", "message": f"speech-to-text failed: {e}"})
            return
        if not text:
            self._empty_streak += 1
            if self._empty_streak >= 2:
                self._empty_streak = 0
                await self.play(next(self._unclear), cache=True)
            return
        self._empty_streak = 0
        reply_task = asyncio.create_task(self.service.respond(self.session, text))
        # Up to two fillers while the model thinks (Gemini can take ~5 s), so the wait
        # sounds like a grandmother fumbling for her pen rather than dead air.
        waits = (FILLER_AFTER_S, SECOND_FILLER_AFTER_S)
        paatti = None
        for wait in waits:
            try:
                paatti = await asyncio.wait_for(asyncio.shield(reply_task), wait)
                break
            except asyncio.TimeoutError:
                await self.play(next(self._fillers), cache=True)
        if paatti is None:
            paatti = await reply_task
        await self.play(paatti["text"])

    # ---- audio out -------------------------------------------------------
    async def _tts(self, text: str, cache: bool = False) -> bytes:
        key = (self.profile.language_code, self.profile.tts_speaker, text, self.out_rate)
        hit = TTS_CACHE.get(key)
        if hit is not None:
            return hit
        wav = await self.sarvam.synthesize(text, language_code=self.profile.language_code,
                                           speaker=self.profile.tts_speaker)
        pcm, rate = pcm_from_wav(wav)
        pcm = resample(pcm, rate, self.out_rate)
        if cache:
            TTS_CACHE.put(key, pcm)
        return pcm

    async def play(self, text: str, cache: bool = False) -> None:
        try:
            pcm = await self._tts(text, cache=cache)
        except (SarvamError, ValueError) as e:
            self.service.hub.publish(self.session.id, {"type": "notice", "message": f"voice output failed: {e}"})
            return
        duration = len(pcm) / 2 / self.out_rate
        start = max(self.clock(), self.play_until)
        self.play_until = start + duration
        await self.send_audio(pcm)
