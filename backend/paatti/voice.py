"""Streaming voice pipeline shared by the Twilio phone line and the browser phone.

caller audio -> VAD -> Sarvam STT -> persona -> Sarvam TTS -> audio back

To hide model latency, a short pre-synthesised filler ("Aiyo, oru nimisham
kanna...") plays if the reply isn't ready quickly, so the pause sounds like an
old lady fumbling rather than a slow machine.
"""

from __future__ import annotations

import asyncio
import itertools
import wave
import logging
import time
from collections import OrderedDict
from typing import Awaitable, Callable

from .audio import VAD, pcm_from_wav, resample, wav_from_pcm
from .personas import get_profile
from .sarvam import SarvamClient, SarvamError
from .speech import speak_stream, split_for_tts, synth_stream
from .service import CallService
from .session import CallSession

log = logging.getLogger(__name__)

STT_RATE = 16000
FILLER_AFTER_S = 1.2  # a filler only covers a genuinely slow (model) reply; most replies never need one


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
        send_text: Callable[[str], Awaitable[None]] | None = None,
        on_stt_unavailable: Callable[[], Awaitable[None]] | None = None,
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
        # Fallbacks for when Sarvam refuses (rate limit, no credits): the phone speaks the line
        # with its own voice, and listens with its own recognition. The call never goes silent.
        self.send_text = send_text
        self.on_stt_unavailable = on_stt_unavailable
        self.stt_local = False
        self._sent: list[str] = []
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
        """Pre-synthesise greeting and fillers so they play instantly (demo lines are
        warmed into the disk cache at server startup)."""
        lines = [self.profile.greeting]  # fillers are made on first use: each clip costs a Sarvam request
        for t in lines:
            try:
                await self._tts(t, cache=True)
            except (SarvamError, ValueError, EOFError, wave.Error):
                break

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
            if self.on_stt_unavailable and not self.stt_local:
                self.stt_local = True  # the phone listens with its own recognition from now on
                await self.on_stt_unavailable()
                log.warning("Sarvam speech-to-text refused (%s); phone switched to on-device recognition", e)
                return
            self.service.hub.publish(self.session.id, {"type": "notice", "message": f"speech-to-text failed: {e}"})
            return
        if not text:
            self._empty_streak += 1
            if self._empty_streak >= 2:
                self._empty_streak = 0
                await self.play(next(self._unclear), cache=True)
            return
        self._empty_streak = 0
        await self.reply_streaming(text)

    async def reply_streaming(self, text: str) -> None:
        """Speak the reply sentence by sentence as it is produced. Scripted, read-back and
        prepared replies arrive instantly and play straight away; only if the first sentence
        takes longer than FILLER_AFTER_S (a model call) does ONE short filler cover the gap.
        Audio is sent strictly in order, so a filler can never overlap the reply."""
        sentences: asyncio.Queue = asyncio.Queue()

        async def produce() -> None:
            try:
                async for sentence in self.service.respond_stream(self.session, text):
                    await sentences.put(sentence)
            finally:
                await sentences.put(None)

        producer = asyncio.create_task(produce())
        try:
            try:
                first = await asyncio.wait_for(sentences.get(), FILLER_AFTER_S)
            except asyncio.TimeoutError:
                await self.play(next(self._fillers), cache=True)
                first = await sentences.get()

            async def stream():
                item = first
                while item is not None:
                    yield item
                    item = await sentences.get()

            self._sent = []
            heard: list[str] = []
            done = {"end": False}

            async def tracked():
                async for item in stream():
                    heard.append(item)
                    yield item
                done["end"] = True

            try:
                await speak_stream(tracked(), self.sarvam, self.profile.language_code, self.profile.tts_speaker,
                                   self._send_chunk)
            except (SarvamError, ValueError, EOFError, wave.Error) as e:
                while not done["end"]:  # collect the rest of what the model writes
                    item = await sentences.get()
                    if item is None:
                        break
                    heard.append(item)
                await self._fallback(heard, e)
        finally:
            await producer

    async def _fallback(self, sentences: list[str], err: Exception) -> None:
        """Sarvam refused mid-reply: speak what is still unsaid with the phone's own voice."""
        chunks = [c for sentence in sentences for c in (split_for_tts(sentence) or [])]
        rest = " ".join(chunks[len(self._sent):]).strip()
        if self.send_text and rest:
            log.warning("Sarvam voice refused (%s); phone speaks the line itself", err)
            try:
                await self.send_text(rest, str(err))
            except TypeError:  # a send_text that takes only the line
                await self.send_text(rest)
        else:
            self.service.hub.publish(self.session.id, {"type": "notice", "message": f"voice output failed: {err}"})

    async def _send_chunk(self, _chunk: str, wav: bytes) -> None:
        self._sent.append(_chunk)
        pcm, rate = pcm_from_wav(wav)
        pcm = resample(pcm, rate, self.out_rate)
        self._queue_playback(pcm)
        await self.send_audio(pcm)

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

    def _queue_playback(self, pcm: bytes) -> None:
        start = max(self.clock(), self.play_until)
        self.play_until = start + len(pcm) / 2 / self.out_rate

    async def play(self, text: str, cache: bool = False) -> None:
        """Speak a line. Short cached lines (greeting, fillers) go whole; replies stream
        sentence by sentence so the first words are heard while the rest is synthesised."""
        try:
            if cache:
                pcm = await self._tts(text, cache=True)
                self._queue_playback(pcm)
                await self.send_audio(pcm)
                return
            self._sent = []
            async for _chunk, wav in synth_stream(self.sarvam, text, self.profile.language_code,
                                                  self.profile.tts_speaker):
                self._sent.append(_chunk)
                pcm, rate = pcm_from_wav(wav)
                pcm = resample(pcm, rate, self.out_rate)
                self._queue_playback(pcm)
                await self.send_audio(pcm)
        except (SarvamError, ValueError, EOFError, wave.Error) as e:
            await self._fallback([text], e)
