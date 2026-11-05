"""Audio helpers for the phone pipeline (pure Python; audioop is gone in 3.13).

PCM here always means 16-bit signed little-endian mono.
"""

from __future__ import annotations

import io
import math
import wave
from array import array
from collections import deque
from typing import Iterator

_BIAS = 0x84
_CLIP = 32635


def _decode_byte(u: int) -> int:
    u = ~u & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + _BIAS) << exponent
    sample -= _BIAS
    return -sample if sign else sample


def _encode_sample(s: int) -> int:
    sign = 0x80 if s < 0 else 0
    if sign:
        s = -s
    s = min(s, _CLIP) + _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (s & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (s >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


_DECODE = array("h", (_decode_byte(u) for u in range(256)))
_ENCODE = bytes(_encode_sample(s - 32768) for s in range(65536))


def _samples(pcm: bytes) -> array:
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return a


def mulaw_decode(data: bytes) -> bytes:
    """G.711 mu-law bytes -> PCM16."""
    return array("h", (_DECODE[b] for b in data)).tobytes()


def mulaw_encode(pcm: bytes) -> bytes:
    """PCM16 -> G.711 mu-law bytes."""
    return bytes(_ENCODE[s + 32768] for s in _samples(pcm))


def wav_from_pcm(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def pcm_from_wav(data: bytes) -> tuple[bytes, int]:
    """WAV bytes -> (mono PCM16, sample rate). Downmixes stereo."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV supported")
        channels, rate = w.getnchannels(), w.getframerate()
        frames = w.readframes(w.getnframes())
    if channels == 1:
        return frames, rate
    s = _samples(frames)
    mono = array("h", (sum(s[i:i + channels]) // channels for i in range(0, len(s), channels)))
    return mono.tobytes(), rate


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Linear-interpolation resampler; averages when downsampling by an integer factor."""
    if src == dst or not pcm:
        return pcm
    s = _samples(pcm)
    if src > dst and src % dst == 0:
        k = src // dst
        return array("h", (sum(s[i:i + k]) // k for i in range(0, len(s) - k + 1, k))).tobytes()
    n_out = int(len(s) * dst / src)
    step = src / dst
    out = array("h")
    last = len(s) - 1
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = s[j]
        b = s[j + 1] if j < last else a
        out.append(int(a + (b - a) * frac))
    return out.tobytes()


def frames_of(pcm: bytes, rate: int, ms: int = 20) -> Iterator[bytes]:
    size = rate * ms // 1000 * 2
    for i in range(0, len(pcm) - size + 1, size):
        yield pcm[i:i + size]


def rms(pcm: bytes) -> float:
    s = _samples(pcm)
    if not s:
        return 0.0
    return math.sqrt(sum(x * x for x in s) / len(s))


class VAD:
    """Energy-based voice activity detector that segments caller utterances.

    feed() takes PCM of any length and returns events:
      ("speech_start", None) when the caller starts talking
      ("utterance", pcm)     when they stop (or after max_utterance_ms)
    """

    def __init__(
        self,
        rate: int,
        frame_ms: int = 20,
        start_ms: int = 100,
        silence_ms: int = 550,
        min_utterance_ms: int = 300,
        max_utterance_ms: int = 15000,
        preroll_ms: int = 200,
        min_threshold: float = 500.0,
    ):
        self.rate = rate
        self.frame_bytes = rate * frame_ms // 1000 * 2
        self.frame_ms = frame_ms
        self.start_frames = max(1, start_ms // frame_ms)
        self.silence_frames = max(1, silence_ms // frame_ms)
        self.min_frames = max(1, min_utterance_ms // frame_ms)
        self.max_frames = max(1, max_utterance_ms // frame_ms)
        self.min_threshold = min_threshold
        self.noise = min_threshold / 3
        self._preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // frame_ms))
        self._buf = b""
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self._voiced_run = 0
        self._silent_run = 0
        self._speech_frames = 0
        self._utt: list[bytes] = []
        self._levels: list[float] = []
        self._pending: list[bytes] = []
        self._buf = b""

    @property
    def threshold(self) -> float:
        return max(self.min_threshold, self.noise * 3)

    def feed(self, pcm: bytes) -> list[tuple[str, bytes | None]]:
        events: list[tuple[str, bytes | None]] = []
        self._buf += pcm
        while len(self._buf) >= self.frame_bytes:
            frame, self._buf = self._buf[: self.frame_bytes], self._buf[self.frame_bytes:]
            events += self._frame(frame)
        return events

    def _frame(self, frame: bytes) -> list[tuple[str, bytes | None]]:
        level = rms(frame)
        voiced = level > self.threshold
        if not self.in_speech:
            if voiced:
                self._voiced_run += 1
                self._pending.append(frame)
                if self._voiced_run >= self.start_frames:
                    self.in_speech = True
                    self._utt = list(self._preroll) + self._pending
                    self._speech_frames = self._voiced_run
                    self._silent_run = 0
                    self._pending = []
                    self._preroll.clear()
                    return [("speech_start", None)]
            else:
                self.noise = 0.95 * self.noise + 0.05 * level
                for f in self._pending:
                    self._preroll.append(f)
                self._pending = []
                self._voiced_run = 0
                self._preroll.append(frame)
            return []

        self._utt.append(frame)
        self._levels.append(level)
        if voiced:
            self._speech_frames += 1
            self._silent_run = 0
        else:
            self._silent_run += 1
        if self._silent_run >= self.silence_frames or len(self._utt) >= self.max_frames:
            pcm = b"".join(self._utt)
            enough = self._speech_frames >= self.min_frames
            forced = len(self._utt) >= self.max_frames
            levels = sorted(self._levels)
            self.in_speech = False
            self._utt, self._levels, self._voiced_run, self._silent_run, self._speech_frames = [], [], 0, 0, 0
            if forced and levels:
                # Speech rises and falls (syllables, pauses); steady line noise is flat. If a forced
                # 15 s cut was flat, it was noise: re-learn the floor from it so we stop hearing it.
                low, mid = levels[len(levels) // 5], levels[len(levels) // 2]
                if mid and low >= 0.6 * mid:
                    self.noise = max(self.noise, low)
                    enough = False
            return [("utterance", pcm)] if enough else []
        return []
