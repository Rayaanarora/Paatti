// Continuous "open line" room tone for the whole call, so the gaps between Paatti's sentences
// sound like a live phone line instead of dead digital silence. Matches the backend's
// audio.phone_line bed: band-limited pink noise (300-3400 Hz) at about -52 dBFS with a faint
// ~5 Hz ceiling-fan swish.

export const LINE_NOISE_RMS = 0.0025 // -52 dBFS

let cached = null

function pinkBuffer(ctx, seconds = 4) {
  if (cached && cached.sampleRate === ctx.sampleRate) return cached
  const n = Math.floor(ctx.sampleRate * seconds)
  const buf = ctx.createBuffer(1, n, ctx.sampleRate)
  const d = buf.getChannelData(0)
  // Paul Kellet's pink filter
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0
  let sum = 0
  for (let i = 0; i < n; i++) {
    const w = Math.random() * 2 - 1
    b0 = 0.99886 * b0 + w * 0.0555179
    b1 = 0.99332 * b1 + w * 0.0750759
    b2 = 0.969 * b2 + w * 0.153852
    b3 = 0.8665 * b3 + w * 0.3104856
    b4 = 0.55 * b4 + w * 0.5329522
    b5 = -0.7616 * b5 - w * 0.016898
    d[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362
    b6 = w * 0.115926
    sum += d[i] * d[i]
  }
  // Crossfade the loop seam so the 4 s loop never clicks.
  const fade = Math.floor(ctx.sampleRate * 0.05)
  for (let i = 0; i < fade; i++) {
    const g = i / fade
    d[i] = d[i] * g + d[n - fade + i] * (1 - g)
  }
  const scale = 1 / Math.sqrt(sum / n || 1)
  for (let i = 0; i < n; i++) d[i] *= scale
  cached = buf
  return buf
}

/** Start the room tone on `ctx`; returns stop(). Safe to call with no ctx. */
export function startLineNoise(ctx, rms = LINE_NOISE_RMS) {
  if (!ctx) return () => {}
  try {
    const src = ctx.createBufferSource()
    src.buffer = pinkBuffer(ctx)
    src.loop = true
    const hp = ctx.createBiquadFilter()
    hp.type = 'highpass'
    hp.frequency.value = 300
    const lp = ctx.createBiquadFilter()
    lp.type = 'lowpass'
    lp.frequency.value = 3400
    const gain = ctx.createGain()
    // The band-limit removes some energy; 1.6x brings the in-band level back to target.
    const level = rms * 1.6
    gain.gain.setValueAtTime(0, ctx.currentTime)
    gain.gain.linearRampToValueAtTime(level, ctx.currentTime + 0.25)
    const fan = ctx.createOscillator()
    fan.frequency.value = 4.6 + Math.random() * 1.2
    const fanDepth = ctx.createGain()
    fanDepth.gain.value = level * 0.18
    fan.connect(fanDepth).connect(gain.gain)
    src.connect(hp).connect(lp).connect(gain).connect(ctx.destination)
    src.start()
    fan.start()
    return () => {
      try {
        gain.gain.cancelScheduledValues(ctx.currentTime)
        gain.gain.setTargetAtTime(0, ctx.currentTime, 0.05)
        src.stop(ctx.currentTime + 0.3)
        fan.stop(ctx.currentTime + 0.3)
      } catch { /* already stopped */ }
    }
  } catch {
    return () => {}
  }
}
