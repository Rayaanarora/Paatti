import { useEffect, useRef, useState } from 'react'
import { apiUrl, wsUrl } from './api.js'
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

/** WhatsApp-style outgoing ringback tone: dual 440 Hz + 480 Hz chime with smooth envelope. */
function ringback(ctx) {
  let stopped = false
  const playPulse = () => {
    if (stopped) return
    try {
      const now = ctx.currentTime
      const osc1 = ctx.createOscillator()
      const osc2 = ctx.createOscillator()
      const gain = ctx.createGain()
      osc1.frequency.value = 440
      osc2.frequency.value = 480
      gain.gain.setValueAtTime(0, now)
      gain.gain.linearRampToValueAtTime(0.12, now + 0.1)
      gain.gain.setValueAtTime(0.12, now + 1.2)
      gain.gain.linearRampToValueAtTime(0, now + 1.4)
      osc1.connect(gain)
      osc2.connect(gain)
      gain.connect(ctx.destination)
      osc1.start(now)
      osc2.start(now)
      osc1.stop(now + 1.5)
      osc2.stop(now + 1.5)
    } catch { /* audio context might be closed */ }
  }
  playPulse()
  const interval = setInterval(playPulse, 3200)
  return () => {
    stopped = true
    clearInterval(interval)
  }
}

export default function PhoneApp({ sessionId: initialId }) {
  const [phase, setPhase] = useState('idle') // idle | ringing | connected | ended | error
  const [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [muted, setMuted] = useState(false)
  const [voice, setVoice] = useState(true)
  const [draft, setDraft] = useState('')
  const [persona, setPersona] = useState('paatti')
  const [subtitle, setSubtitle] = useState('')
  const [paattiSpeaking, setPaattiSpeaking] = useState(false)
  const usedInitial = useRef(false)
  const refs = useRef({ ctx: null, ws: null, stream: null, node: null, sources: [], next: 0, stopRing: null, buf: [], bufLen: 0, muted: false, voice: true, rec: null })
  refs.current.muted = muted

  useEffect(() => {
    if (phase !== 'connected') return undefined
    const id = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [phase])

  useEffect(() => () => hangup(true), []) // eslint-disable-line react-hooks/exhaustive-deps

  // Strictly in arrival order: async decoding must not reorder filler and reply chunks.
  const playChain = useRef(Promise.resolve())
  const playWav = (b64) => {
    setPaattiSpeaking(true)
    playChain.current = playChain.current.then(() => scheduleWav(b64)).catch(() => {
      setPaattiSpeaking(false)
    })
  }

  const scheduleWav = async (b64) => {
    const r = refs.current
    if (!r.ctx) return
    if (r.ctx.state === 'suspended') {
      try { await r.ctx.resume() } catch {}
    }
    const raw = atob(b64)
    const bytes = new Uint8Array(raw.length)
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)

    // Method 1: Web Audio Context (resilient clone)
    try {
      const buffer = await r.ctx.decodeAudioData(bytes.buffer.slice(0))
      const src = r.ctx.createBufferSource()
      src.buffer = buffer
      src.connect(r.ctx.destination)
      const at = Math.max(r.ctx.currentTime, r.next)
      src.start(at)
      r.next = at + buffer.duration
      r.sources.push(src)
      src.onended = () => {
        r.sources = r.sources.filter((s) => s !== src)
        if (r.sources.length === 0) setPaattiSpeaking(false)
      }
      return
    } catch {
      // Fall through to HTML5 Audio fallback
    }

    // Method 2: HTML5 Audio fallback for mobile phones where Web Audio is muted/blocked
    try {
      const blob = new Blob([bytes], { type: 'audio/wav' })
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => setPaattiSpeaking(false)
      await audio.play()
    } catch {
      setPaattiSpeaking(false)
    }
  }

  const clearAudio = () => {
    const r = refs.current
    r.sources.forEach((s) => { try { s.stop() } catch { /* ended */ } })
    r.sources = []
    r.next = 0
    setPaattiSpeaking(false)
  }

  const speakText = (text, lang) => {
    setSubtitle(text)
    setPaattiSpeaking(true)
    if (!window.speechSynthesis) {
      setTimeout(() => setPaattiSpeaking(false), 2500)
      return
    }
    const u = new SpeechSynthesisUtterance(text)
    u.lang = lang || 'ta-IN'
    u.rate = 1.05
    u.pitch = 0.9
    u.onend = u.onerror = () => setPaattiSpeaking(false)
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
        const res = await fetch(apiUrl('/api/sessions'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ channel: 'web-phone' }) })
        id = (await res.json()).session_id
      } catch {
        setError('Could not reach Paatti. Check the connection.')
        setPhase('error')
        r.stopRing?.()
        return
      }
    }

    const ws = new WebSocket(wsUrl(`/ws/phone/${id}`))
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
      } else if (msg.type === 'audio') {
        whenAnswered(() => playWav(msg.data))
      } else if (msg.type === 'clear') {
        clearAudio()
      } else if (msg.type === 'paatti_text') {
        whenAnswered(() => {
          setSubtitle(msg.text)
          speakText(msg.text, msg.lang)
        })
      } else if (msg.type === 'turn') {
        if (msg.turn.role === 'paatti') {
          setSubtitle(msg.turn.text)
          setPaattiSpeaking(true)
        }
      }
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

  const DEMO_STEPS = [
    {
      step: 1,
      tag: 'STEP 1 · ACCUSATION',
      target: 'Agency: CBI',
      line: 'This is Inspector Rajesh Verma from CBI. Your Aadhaar is linked to a money-laundering case.',
    },
    {
      step: 2,
      tag: 'STEP 2 · DEMAND MULE TRANSFER',
      target: 'Account: 5021 0034 7781 9920 · Rs 4,50,000',
      line: 'To clear your name, transfer Rs 4,50,000 to safe account: 5021 0034 7781 9920.',
    },
    {
      step: 3,
      tag: 'STEP 3 · CONFIRM & LOCK INTEL',
      target: 'IFSC: HDFC0001234 (Locks Green)',
      line: 'Yes correct, the IFSC is HDFC0001234. Transfer immediately.',
    },
    {
      step: 4,
      tag: 'OPTIONAL · UPI CLIMAX',
      target: 'UPI: rbi.verify@ybl',
      line: 'Fine, send it by UPI to rbi.verify@ybl immediately.',
    },
  ]

  const [activeStep, setActiveStep] = useState(0)

  const handleSendStep = (idx, text) => {
    setActiveStep(idx + 1)
    send({ type: 'text', text })
  }

  const name = persona === 'dadi' ? 'Sharda Dadi' : 'Kamala Paatti'
  return (
    <div className={`phone ${phase}`}>
      <div className="wa-header">
        <span className="wa-back">〈</span>
        <div className="wa-header-center">
          <span className="wa-lock">🔒</span>
          <span>WhatsApp Voice Call · End-to-end encrypted</span>
        </div>
        <span className="wa-user-add">👤⁺</span>
      </div>

      <div className="phone-top">
        <div className="avatar-wrapper">
          <div className="avatar" aria-hidden="true">👵</div>
          {phase === 'connected' && <span className="avatar-live-badge">LIVE</span>}
        </div>
        <h1 className="contact-name">{name}</h1>
        <p className="phone-subtitle">Retired Teacher · Mylapore, Chennai</p>
        <p className="phone-status">
          {phase === 'idle' && 'Tap Call to start'}
          {phase === 'ringing' && 'Ringing…'}
          {phase === 'connected' && formatClock(seconds)}
          {phase === 'ended' && `Call ended · ${formatClock(seconds)}`}
          {phase === 'error' && 'Call failed'}
        </p>

        {phase === 'connected' && (
          <>
            {paattiSpeaking ? (
              <div className="turn-indicator speaking">
                <span className="speaker-wave-pulse">🔊</span>
                <div className="turn-text">
                  <strong>PAATTI IS SPEAKING...</strong>
                  <span>Please listen to her response</span>
                </div>
              </div>
            ) : (
              <div className="turn-indicator your-turn">
                <span className="mic-dot pulse"></span>
                <div className="turn-text">
                  <strong>YOUR TURN TO SPEAK NOW!</strong>
                  <span>Read highlighted Beat {activeStep || 1} into mic, or tap card</span>
                </div>
              </div>
            )}

            {subtitle && (
              <div className={`live-speech-bubble ${paattiSpeaking ? 'active' : 'idle'}`}>
                <div className="speech-bubble-header">
                  <span className="bubble-speaker">👵 Kamala Paatti:</span>
                  {paattiSpeaking && <span className="bubble-live-tag">Speaking now</span>}
                </div>
                <p className="speech-bubble-content">"{subtitle}"</p>
              </div>
            )}
          </>
        )}
        {error && <p className="phone-error">{error}</p>}
      </div>

      {phase === 'connected' && (
        <div className="phone-demo-sheet">
          <div className="demo-sheet-header">
            <span>📋 JUDGE DEMO SCRIPT</span>
            <span className="demo-badge">Read aloud OR Tap</span>
          </div>

          <div className="demo-cards-list">
            {DEMO_STEPS.map((s, idx) => {
              const isDone = activeStep > idx + 1
              const isCurrent = activeStep === idx + 1 || (activeStep === 0 && idx === 0)
              return (
                <div
                  key={s.step}
                  className={`script-card ${isCurrent ? 'current' : ''} ${isDone ? 'done' : ''}`}
                  onClick={() => handleSendStep(idx, s.line)}
                  role="button"
                  tabIndex={0}
                >
                  <div className="script-card-header">
                    <span className="script-tag">{s.tag}</span>
                    <span className="script-target">{s.target}</span>
                  </div>
                  <p className="script-speech-text">
                    "{s.line}"
                  </p>
                  <div className="script-card-footer">
                    <span className="script-tap-hint">
                      {isDone ? '✓ Spoken / Sent' : 'Tap to send line 📤'}
                    </span>
                    <span className="script-num-pill">Beat {s.step}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {phase === 'connected' && !voice && (
        <form className="phone-type" onSubmit={sendDraft}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Type what you say…" aria-label="Your line" />
          <button aria-label="Send">➤</button>
        </form>
      )}

      <div className="phone-actions">
        {(phase === 'idle' || phase === 'ended' || phase === 'error') && (
          <div className="action-col">
            <button className="round call pulse-call" onClick={call} aria-label="Call">📞</button>
            <span className="btn-label-primary">Call Paatti</span>
          </div>
        )}
        {(phase === 'ringing' || phase === 'connected') && (
          <>
            <div className="action-col">
              <button
                className={`round ghost ${muted ? 'muted-on' : ''}`}
                onClick={() => setMuted((m) => !m)}
                aria-pressed={muted}
                aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
              >
                {muted ? '🔇' : '🎙️'}
              </button>
              <span className="btn-label">{muted ? 'Unmute' : 'Mute Mic'}</span>
              <span className="btn-sublabel">{muted ? 'Muted' : 'Live'}</span>
            </div>
            <div className="action-col">
              <button className="round hang" onClick={() => hangup()} aria-label="End call">📞</button>
              <span className="btn-label end-label">End Call</span>
              <span className="btn-sublabel">&nbsp;</span>
            </div>
            <div className="action-col">
              <button className="round ghost speaker-btn" aria-label="Speaker" onClick={() => {}}>
                🔊
              </button>
              <span className="btn-label">Speaker</span>
              <span className="btn-sublabel">On</span>
            </div>
          </>
        )}
      </div>
      <p className="phone-foot">Paatti AI Honeypot · Real-time Laptop Sync Active</p>
    </div>
  )
}
