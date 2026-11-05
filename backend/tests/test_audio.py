import math
import struct

from paatti.audio import (
    VAD,
    frames_of,
    mulaw_decode,
    mulaw_encode,
    pcm_from_wav,
    resample,
    rms,
    wav_from_pcm,
)


def tone(ms, rate=8000, amp=8000, freq=440):
    n = rate * ms // 1000
    return struct.pack(f"<{n}h", *(int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))


def silence(ms, rate=8000):
    return b"\x00\x00" * (rate * ms // 1000)


def test_mulaw_known_values_and_roundtrip():
    assert mulaw_encode(b"\x00\x00") == b"\xff"
    assert mulaw_decode(b"\xff") == b"\x00\x00"
    pcm = tone(20)
    back = mulaw_decode(mulaw_encode(pcm))
    a = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    b = struct.unpack(f"<{len(back) // 2}h", back)
    # mu-law is lossy but within ~3% of full scale at this amplitude
    assert max(abs(x - y) for x, y in zip(a, b)) < 400


def test_mulaw_clips_extremes():
    pcm = struct.pack("<2h", 32767, -32768)
    out = struct.unpack("<2h", mulaw_decode(mulaw_encode(pcm)))
    assert out[0] > 30000 and out[1] < -30000


def test_wav_roundtrip_and_stereo_downmix():
    pcm = tone(50, rate=16000)
    assert pcm_from_wav(wav_from_pcm(pcm, 16000)) == (pcm, 16000)
    stereo = struct.pack("<4h", 100, 300, -100, -300)
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(stereo)
    mono, rate = pcm_from_wav(buf.getvalue())
    assert rate == 8000 and struct.unpack("<2h", mono) == (200, -200)


def test_resample_lengths():
    pcm = tone(100, rate=24000)
    assert len(resample(pcm, 24000, 8000)) == 800 * 2
    assert len(resample(tone(100), 8000, 16000)) == 1600 * 2
    assert resample(pcm, 24000, 24000) == pcm
    assert resample(b"", 8000, 16000) == b""


def test_frames_and_rms():
    assert len(list(frames_of(silence(100), 8000, 20))) == 5
    assert rms(silence(20)) == 0
    assert rms(tone(20)) > 4000


def test_vad_segments_one_utterance():
    vad = VAD(rate=8000)
    events = []
    for chunk in frames_of(silence(300) + tone(600) + silence(900), 8000, 20):
        events += vad.feed(chunk)
    kinds = [e[0] for e in events]
    assert kinds == ["speech_start", "utterance"]
    utt = events[1][1]
    # tone plus pre-roll plus trailing silence, roughly 0.6-1.6 s
    assert 0.6 <= len(utt) / 2 / 8000 <= 1.6


def test_vad_ignores_short_blips():
    vad = VAD(rate=8000)
    events = []
    for chunk in frames_of(silence(200) + tone(40) + silence(1000), 8000, 20):
        events += vad.feed(chunk)
    assert all(e[0] != "utterance" for e in events)


def test_vad_force_cuts_long_speech():
    vad = VAD(rate=8000, max_utterance_ms=1000)
    events = []
    for chunk in frames_of(tone(2500), 8000, 20):
        events += vad.feed(chunk)
    assert sum(1 for e in events if e[0] == "utterance") >= 2


def test_vad_reset_drops_partial():
    vad = VAD(rate=8000)
    for chunk in frames_of(tone(300), 8000, 20):
        vad.feed(chunk)
    vad.reset()
    events = []
    for chunk in frames_of(silence(1000), 8000, 20):
        events += vad.feed(chunk)
    assert events == []
