import { useEffect, useRef, useState } from 'react'
import { formatClock } from './state.js'

// Mic capture runs in an AudioWorklet; frames are downsampled to 16 kHz PCM16 and
// streamed to /ws/phone/:id, which runs the same voice pipeline as the phone line.
const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (ch) this.port.postMessage(ch.slice(0))
    return true
  }
}
registerProcessor('paatti-tap', Tap)
`
const TARGET_RATE = 16000
const SpeechRecognition = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition)

function toBase64(bytes) {
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

/** Stateful downsampler: carries the fractional read position across 128-sample worklet chunks. */
function makeDownsampler(fromRate) {
  const ratio = fromRate / TARGET_RATE
  let carry = new Float32Array(0)
  let pos = 0 // fractional index into (carry + chunk)
  return (chunk) => {
    const input = new Float32Array(carry.length + chunk.length)
    input.set(carry)
    input.set(chunk, carry.length)
    const out = []
    while (pos + ratio <= input.length) {
      const start = Math.floor(pos)
      const end = Math.floor(pos + ratio)
      let sum = 0
      for (let j = start; j < end; j++) sum += input[j]
      const v = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)))
      out.push(v < 0 ? v * 0x8000 : v * 0x7fff)
      pos += ratio
    }
    const keepFrom = Math.floor(pos)
    carry = input.slice(keepFrom)
    pos -= keepFrom
    return Int16Array.from(out)
  }
}

/** Indian ringback: 400 Hz, 0.4 s on, 0.2 s off, 0.4 s on, 2 s off. */
function ringback(ctx) {
  const gain = ctx.createGain()
  gain.gain.value = 0
  gain.connect(ctx.destination)
  const osc = ctx.createOscillator()
  osc.frequency.value = 400
  osc.connect(gain)
  osc.start()
  const t0 = ctx.currentTime
  for (let i = 0; i < 6; i++) {
    const t = t0 + i * 3
    gain.gain.setValueAtTime(0.15, t)
    gain.gain.setValueAtTime(0, t + 0.4)
    gain.gain.setValueAtTime(0.15, t + 0.6)
    gain.gain.setValueAtTime(0, t + 1.0)
  }
  return () => { try { osc.stop() } catch { /* already stopped */ } gain.disconnect() }
}

export default function PhoneApp({ sessionId: initialId }) {
  const [phase, setPhase] = useState('idle') // idle | ringing | connected | ended | error
  const [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [muted, setMuted] = useState(false)
  const [voice, setVoice] = useState(true)
  const [draft, setDraft] = useState('')
  const [persona, setPersona] = useState('paatti')
  const usedInitial = useRef(false)
  const refs = useRef({ ctx: null, ws: null, stream: null, node: null, sources: [], next: 0, stopRing: null, buf: [], bufLen: 0, muted: false, voice: true, rec: null })
  refs.current.muted = muted

  useEffect(() => {
    if (phase !== 'connected') return undefined
    const id = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [phase])

  useEffect(() => () => hangup(true), []) // eslint-disable-line react-hooks/exhaustive-deps

  const playWav = async (b64) => {
    const r = refs.current
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
    const buffer = await r.ctx.decodeAudioData(bytes.buffer)
    const src = r.ctx.createBufferSource()
    src.buffer = buffer
    src.connect(r.ctx.destination)
    const at = Math.max(r.ctx.currentTime, r.next)
    src.start(at)
    r.next = at + buffer.duration
    r.sources.push(src)
    src.onended = () => { r.sources = r.sources.filter((s) => s !== src) }
  }

  const clearAudio = () => {
    const r = refs.current
    r.sources.forEach((s) => { try { s.stop() } catch { /* ended */ } })
    r.sources = []
    r.next = 0
  }

  const speakText = (text, lang) => {
    if (!window.speechSynthesis) return
    const u = new SpeechSynthesisUtterance(text)
    u.lang = lang || 'ta-IN'
    u.rate = 0.85
    u.pitch = 0.85
    window.speechSynthesis.speak(u)
  }

  const send = (msg) => {
    const ws = refs.current.ws
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
  }

  const call = async () => {
    setError('')
    const r = refs.current
    try {
      r.ctx = new (window.AudioContext || window.webkitAudioContext)()
      await r.ctx.resume()
    } catch {
      setError('Audio is not available in this browser.')
      setPhase('error')
      return
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone needs HTTPS. Open this page through the https:// link.')
      setPhase('error')
      return
    }
    try {
      r.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
    } catch {
      setError('Microphone permission was denied.')
      setPhase('error')
      return
    }
    setPhase('ringing')
    navigator.vibrate?.(200)
    r.stopRing = ringback(r.ctx)
    const ringUntil = Date.now() + 3000 // ring briefly so it feels like a real call

    // The QR session is single-use: a redial (or a dropped call) starts a fresh call.
    let id = !usedInitial.current ? initialId : null
    usedInitial.current = true
    if (!id) {
      try {
        const res = await fetch('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ channel: 'web-phone' }) })
        id = (await res.json()).session_id
      } catch {
        setError('Could not reach Paatti. Check the connection.')
        setPhase('error')
        r.stopRing?.()
        return
      }
    }

    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/phone/${id}`)
    r.ws = ws
    const pending = []
    const answer = () => {
      r.stopRing?.()
      r.stopRing = null
      setPhase('connected')
      setSeconds(0)
      send({ type: 'answered' }) // server greets now, so its echo guard matches what we play
      pending.splice(0).forEach((fn) => fn())
    }
    const whenAnswered = (fn) => (r.stopRing ? pending.push(fn) : fn())

    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'connected') {
        r.voice = msg.voice
        setVoice(msg.voice)
        setPersona(msg.persona)
        setTimeout(answer, Math.max(0, ringUntil - Date.now()))
        if (msg.voice) startStreaming()
        else startRecognition()
      } else if (msg.type === 'audio') whenAnswered(() => playWav(msg.data))
      else if (msg.type === 'clear') clearAudio()
      else if (msg.type === 'paatti_text') whenAnswered(() => speakText(msg.text, msg.lang))
    }
    ws.onclose = () => { if (refs.current.ws === ws) end() }
    ws.onerror = () => { setError('Call dropped.'); }
  }

  const startStreaming = async () => {
    const r = refs.current
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }))
    await r.ctx.audioWorklet.addModule(url)
    const srcNode = r.ctx.createMediaStreamSource(r.stream)
    const tap = new AudioWorkletNode(r.ctx, 'paatti-tap')
    const downsample = makeDownsampler(r.ctx.sampleRate)
    tap.port.onmessage = (e) => {
      const pcm = downsample(e.data)
      if (r.muted) return
      r.buf.push(pcm)
      r.bufLen += pcm.length
      if (r.bufLen >= TARGET_RATE / 10) { // ~100 ms per message
        const joined = new Int16Array(r.bufLen)
        let o = 0
        r.buf.forEach((b) => { joined.set(b, o); o += b.length })
        r.buf = []
        r.bufLen = 0
        send({ type: 'pcm', data: toBase64(new Uint8Array(joined.buffer)) })
      }
    }
    srcNode.connect(tap)
    r.node = { srcNode, tap }
  }

  // Fallback when the server has no Sarvam key: on-device recognition (Chrome on Android).
  const startRecognition = () => {
    if (!SpeechRecognition) return
    const rec = new SpeechRecognition()
    rec.lang = 'en-IN'
    rec.continuous = true
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal && !refs.current.muted) send({ type: 'text', text: e.results[i][0].transcript })
      }
    }
    rec.onend = () => { if (refs.current.ws?.readyState === WebSocket.OPEN) { try { rec.start() } catch { /* running */ } } }
    try { rec.start() } catch { /* running */ }
    refs.current.rec = rec
  }

  const end = () => {
    const r = refs.current
    r.stopRing?.()
    r.stopRing = null
    clearAudio()
    try { r.rec?.stop() } catch { /* stopped */ }
    r.node?.srcNode.disconnect()
    r.node?.tap.disconnect()
    r.stream?.getTracks().forEach((t) => t.stop())
    window.speechSynthesis?.cancel()
    setPhase((p) => (p === 'error' ? p : 'ended'))
  }

  const hangup = (silent = false) => {
    send({ type: 'hangup' })
    refs.current.ws?.close()
    if (!silent) end()
  }

  const sendDraft = (e) => {
    e.preventDefault()
    if (draft.trim()) send({ type: 'text', text: draft.trim() })
    setDraft('')
  }

  const name = persona === 'dadi' ? 'Sharda Dadi' : 'Kamala Paatti'
  return (
    <div className={`phone ${phase}`}>
      <div className="phone-top">
        <div className="avatar" aria-hidden="true">{name[0]}</div>
        <h1>{name}</h1>
        <p className="phone-status">
          {phase === 'idle' && 'Mobile · India'}
          {phase === 'ringing' && 'Calling…'}
          {phase === 'connected' && formatClock(seconds)}
          {phase === 'ended' && `Call ended · ${formatClock(seconds)}`}
          {phase === 'error' && 'Call failed'}
        </p>
        {error && <p className="phone-error">{error}</p>}
      </div>

      {phase === 'connected' && !voice && (
        <form className="phone-type" onSubmit={sendDraft}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Type what you say…" aria-label="Your line" />
          <button aria-label="Send">➤</button>
        </form>
      )}

      <div className="phone-actions">
        {(phase === 'idle' || phase === 'ended' || phase === 'error') && (
          <button className="round call" onClick={call} aria-label="Call">📞</button>
        )}
        {(phase === 'ringing' || phase === 'connected') && (
          <>
            <button className={`round ghost ${muted ? 'on' : ''}`} onClick={() => setMuted((m) => !m)} aria-pressed={muted} aria-label="Mute">
              {muted ? '🔇' : '🎙️'}
            </button>
            <button className="round hang" onClick={() => hangup()} aria-label="End call">📞</button>
          </>
        )}
      </div>
      <p className="phone-foot">Paatti honeypot demo line</p>
    </div>
  )
}
