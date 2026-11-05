import { useEffect, useRef, useState } from 'react'
import { apiUrl, wsUrl } from './api.js'
import { formatClock } from './state.js'
import { Icon, initials } from './icons.jsx'
import { startLineNoise } from './lineNoise.js'

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

const DEMO_STEPS = [
  {
    step: 1,
    tag: 'STEP 1 · ACCUSATION',
    target: 'Agency: CBI',
    line: 'This is Inspector Rajesh Verma from CBI. Your Aadhaar is linked to a money-laundering case.',
  },
  {
    step: 2,
    tag: 'STEP 2 · MULE TRANSFER',
    target: 'Account · Rs 4,50,000',
    line: 'To clear your name, transfer Rs 4,50,000 to safe account: 5021 0034 7781 9920.',
  },
  {
    step: 3,
    tag: 'STEP 3 · CONFIRM & LOCK',
    target: 'IFSC: HDFC0001234',
    line: 'Yes correct, the IFSC is HDFC0001234. Transfer immediately.',
  },
  {
    step: 4,
    tag: 'OPTIONAL · UPI',
    target: 'UPI: rbi.verify@ybl',
    line: 'Fine, send it by UPI to rbi.verify@ybl immediately.',
  },
]

const ECHO_TAIL_S = 0.35 // keep the mic closed this long after Paatti's audio ends
const MAX_RETRIES = 6
const PING_MS = 15000

export default function PhoneApp({ sessionId: initialId }) {
  const [phase, setPhase] = useState('idle') // idle | ringing | connected | reconnecting | ended | error
  const [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [muted, setMuted] = useState(false)
  const [voice, setVoice] = useState(true)
  const [noMic, setNoMic] = useState(false)
  const [draft, setDraft] = useState('')
  const [who, setWho] = useState({ name: 'Kamala Paatti', tagline: 'Retired Teacher · Mylapore, Chennai', avatar: '👵' })
  const [subtitle, setSubtitle] = useState('')
  const [paattiSpeaking, setPaattiSpeaking] = useState(false)
  const [beatsSent, setBeatsSent] = useState(0)
  const usedInitial = useRef(false)
  const refs = useRef({
    ctx: null, ws: null, stream: null, node: null, sources: [], next: 0, htmlPlaying: 0, stopRing: null,
    buf: [], bufLen: 0, muted: false, voice: true, rec: null, id: null, hungUp: true, retries: 0,
    ping: null, retryTimer: null, mediaStarted: false, scammerTurns: new Set(),
  })
  refs.current.muted = muted

  useEffect(() => {
    if (phase !== 'connected' && phase !== 'reconnecting') return undefined
    const id = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [phase])

  // Mobile browsers suspend audio and may kill the socket when the screen locks or the tab
  // goes to the background: resume audio and rejoin the call as soon as it is visible again.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      const r = refs.current
      if (r.ctx?.state === 'suspended') r.ctx.resume().catch(() => {})
      if (!r.hungUp && r.id && (!r.ws || r.ws.readyState > WebSocket.OPEN)) {
        clearTimeout(r.retryTimer)
        openSocket(r.id)
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Show who is about to answer (the dashboard may have picked any persona, or a random one).
  useEffect(() => {
    if (!initialId) return
    Promise.all([
      fetch(apiUrl(`/api/sessions/${initialId}`)).then((r) => (r.ok ? r.json() : null)),
      fetch(apiUrl('/api/health')).then((r) => (r.ok ? r.json() : null)),
    ]).then(([snap, health]) => {
      const p = health?.personas?.find((x) => x.key === snap?.persona)
      if (snap?.persona) refs.current.persona = snap.persona
      if (p) setWho({ name: p.name, tagline: p.tagline || '', avatar: p.avatar || '👵' })
    }).catch(() => { /* keep the default card */ })
  }, [initialId])

  // Keep the next script step on screen on small phones.
  useEffect(() => {
    document.querySelector('.script-card.current')?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [beatsSent])

  useEffect(() => () => hangup(true), []) // eslint-disable-line react-hooks/exhaustive-deps

  /** True while Paatti's voice is (or is about to be) coming out of the speaker. */
  const isPlaying = () => {
    const r = refs.current
    if (r.htmlPlaying > 0) return true
    if (window.speechSynthesis?.speaking) return true
    return Boolean(r.ctx) && r.ctx.currentTime < r.next + ECHO_TAIL_S
  }

  const settleSpeaking = () => {
    const r = refs.current
    if (r.sources.length === 0 && r.htmlPlaying === 0) setPaattiSpeaking(false)
  }

  // Strictly in arrival order: async decoding must not reorder filler and reply chunks.
  const playChain = useRef(Promise.resolve())
  const playWav = (b64) => {
    setPaattiSpeaking(true)
    playChain.current = playChain.current.then(() => scheduleWav(b64)).catch(settleSpeaking)
  }

  const scheduleWav = async (b64) => {
    const r = refs.current
    if (!r.ctx) return
    if (r.ctx.state === 'suspended') {
      try { await r.ctx.resume() } catch { /* needs a tap */ }
    }
    const raw = atob(b64)
    const bytes = new Uint8Array(raw.length)
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)

    try {
      const buffer = await r.ctx.decodeAudioData(bytes.buffer.slice(0))
      const src = r.ctx.createBufferSource()
      src.buffer = buffer
      src.connect(r.ctx.destination)
      const at = Math.max(r.ctx.currentTime + 0.02, r.next)
      src.start(at)
      r.next = at + buffer.duration
      r.sources.push(src)
      src.onended = () => {
        r.sources = r.sources.filter((s) => s !== src)
        settleSpeaking()
      }
      return
    } catch {
      // Fall through to HTML5 audio for browsers where Web Audio decoding fails.
    }

    // HTML5 fallback, played to the end before the next chunk so chunks never overlap.
    const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }))
    r.htmlPlaying += 1
    try {
      await new Promise((resolve) => {
        const audio = new Audio(url)
        audio.onended = audio.onerror = resolve
        audio.play().catch(resolve)
      })
    } finally {
      r.htmlPlaying -= 1
      URL.revokeObjectURL(url)
      settleSpeaking()
    }
  }

  const clearAudio = () => {
    const r = refs.current
    r.sources.forEach((s) => { try { s.stop() } catch { /* ended */ } })
    r.sources = []
    r.next = 0
    settleSpeaking()
  }

  const speakText = (text, lang) => {
    setSubtitle(text)
    if (!window.speechSynthesis) return
    setPaattiSpeaking(true)
    const u = new SpeechSynthesisUtterance(text)
    u.lang = lang || 'ta-IN'
    u.rate = 1.3
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
    // No mic is not fatal: the call still runs, the caller taps the script lines or types.
    // (Mic needs HTTPS, and some in-app browsers, e.g. a QR scanner's, have none.)
    r.stream = null
    let micNote = ''
    if (!navigator.mediaDevices?.getUserMedia) {
      micNote = 'No microphone here (it needs the https:// link in Chrome or Safari). Tap the lines or type instead.'
    } else {
      try {
        r.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      } catch {
        micNote = 'Microphone is blocked. Allow it in the browser settings, or tap the lines / type instead.'
      }
    }
    setNoMic(!r.stream)
    setPhase('ringing')
    setSubtitle('')
    setBeatsSent(0)
    r.scammerTurns = new Set()
    navigator.vibrate?.(200)
    r.stopRing = ringback(r.ctx)
    r.ringUntil = Date.now() + 3000 // ring briefly so it feels like a real call

    // The QR session is single-use: a redial starts a fresh call.
    let id = !usedInitial.current ? initialId : null
    usedInitial.current = true
    if (!id) id = await newSession()
    if (!id) return
    r.id = id
    r.hungUp = false
    r.retries = 0
    r.everConnected = false
    r.freshTried = false
    r.mediaStarted = false
    if (micNote) setError(micNote)
    openSocket(id)
  }

  /** Start a brand-new call on the server (redial, or the QR's call no longer exists). */
  const newSession = async () => {
    const r = refs.current
    try {
      const res = await fetch(apiUrl('/api/sessions'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel: 'web-phone', persona: r.persona || undefined }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()).session_id
    } catch {
      setError('Could not reach Paatti. Check that the laptop server is running and this link is current.')
      setPhase('error')
      r.stopRing?.()
      return null
    }
  }

  const answer = () => {
    const r = refs.current
    r.stopRing?.()
    r.stopRing = null
    if (!r.stopLine) r.stopLine = startLineNoise(r.ctx) // the open-line hiss of a real call
    setPhase('connected')
    send({ type: 'answered' }) // server greets now, so its echo guard matches what we play
    r.pending.splice(0).forEach((fn) => fn())
  }

  /** Open (or re-open after a drop) the call socket for session `id`. */
  const openSocket = (id) => {
    const r = refs.current
    clearInterval(r.ping)
    const ws = new WebSocket(wsUrl(`/ws/phone/${id}`))
    r.ws = ws
    r.pending = r.pending || []
    const whenAnswered = (fn) => (r.stopRing ? r.pending.push(fn) : fn())

    ws.onopen = () => {
      r.ping = setInterval(() => send({ type: 'ping' }), PING_MS)
    }
    ws.onmessage = (ev) => {
      if (r.ws !== ws) return
      const msg = JSON.parse(ev.data)
      if (msg.type === 'connected') {
        r.retries = 0
        r.everConnected = true
        r.persona = msg.persona
        r.voice = msg.voice
        setVoice(msg.voice)
        if (msg.name) setWho({ name: msg.name, tagline: msg.tagline || '', avatar: msg.avatar || '👵' })
        if (r.stream) setError('') // keep the no-mic note visible
        if (msg.resumed) {
          if (!r.stopLine) r.stopLine = startLineNoise(r.ctx)
          setPhase('connected')
          send({ type: 'answered' })
        } else {
          setTimeout(answer, Math.max(0, (r.ringUntil || 0) - Date.now()))
        }
        if (!r.mediaStarted) {
          r.mediaStarted = true
          if (!r.stream) { /* tap / type only */ } else if (msg.voice) {
            startStreaming().catch(() => { setNoMic(true); setError('Microphone streaming failed; tap the lines or type instead.') })
          } else startRecognition()
        }
      } else if (msg.type === 'audio') {
        whenAnswered(() => playWav(msg.data))
      } else if (msg.type === 'clear') {
        clearAudio()
      } else if (msg.type === 'paatti_text') {
        whenAnswered(() => speakText(msg.text, msg.lang))
      } else if (msg.type === 'turn') {
        const t = msg.turn
        if (t.role === 'paatti') setSubtitle(t.text)
        else if (t.role === 'scammer' && !r.scammerTurns.has(t.index)) {
          r.scammerTurns.add(t.index)
          setBeatsSent(r.scammerTurns.size)
        }
      } else if (msg.type === 'notice') {
        setError(msg.message)
      } else if (msg.type === 'ended') {
        r.hungUp = true
        end()
      }
    }
    ws.onclose = (ev) => {
      if (r.ws !== ws) return
      clearInterval(r.ping)
      if (r.hungUp) return end()
      if (ev.code === 4404 && !r.everConnected && !r.freshTried) {
        // The QR's call is gone (server restarted or the call already ended): start a new one.
        r.freshTried = true
        newSession().then((fresh) => {
          if (!fresh || r.hungUp) return
          r.id = fresh
          openSocket(fresh)
        })
        return
      }
      if (ev.code === 4404 || r.retries >= MAX_RETRIES) {
        r.hungUp = true
        setError(ev.code === 4404 ? 'This call has ended. Tap Call to start a new one.'
          : 'Lost the connection to Paatti. Check the internet, then tap Call to start again.')
        return end()
      }
      // Dropped (network blip, screen lock): rejoin the same call; the server holds it for 30 s.
      r.retries += 1
      setPhase('reconnecting')
      r.retryTimer = setTimeout(() => openSocket(id), Math.min(8000, 500 * 2 ** r.retries))
    }
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
      // Mic closed while muted or while Paatti is talking: on a speakerphone her own voice
      // would otherwise be transcribed as the caller and she would answer herself.
      if (r.muted || isPlaying()) {
        r.buf = []
        r.bufLen = 0
        return
      }
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
        if (e.results[i].isFinal && !refs.current.muted && !isPlaying()) send({ type: 'text', text: e.results[i][0].transcript })
      }
    }
    rec.onend = () => { if (!refs.current.hungUp) { try { rec.start() } catch { /* running */ } } }
    try { rec.start() } catch { /* running */ }
    refs.current.rec = rec
  }

  const end = () => {
    const r = refs.current
    clearInterval(r.ping)
    clearTimeout(r.retryTimer)
    r.stopRing?.()
    r.stopRing = null
    r.stopLine?.()
    r.stopLine = null
    r.pending = []
    clearAudio()
    try { r.rec?.stop() } catch { /* stopped */ }
    r.rec = null
    r.node?.srcNode.disconnect()
    r.node?.tap.disconnect()
    r.node = null
    r.stream?.getTracks().forEach((t) => t.stop())
    r.stream = null
    window.speechSynthesis?.cancel()
    setPaattiSpeaking(false)
    setPhase((p) => (p === 'error' ? p : 'ended'))
  }

  const hangup = (silent = false) => {
    const r = refs.current
    r.hungUp = true
    send({ type: 'hangup' })
    r.ws?.close()
    if (!silent) end()
  }

  const sendDraft = (e) => {
    e.preventDefault()
    if (draft.trim()) send({ type: 'text', text: draft.trim() })
    setDraft('')
  }

  const sendBeat = (line) => {
    if (phase === 'connected') send({ type: 'text', text: line })
  }

  const live = phase === 'connected' || phase === 'reconnecting'
  const nextBeat = Math.min(beatsSent, DEMO_STEPS.length - 1)
  const { name } = who
  return (
    <div className={`phone ${phase}`}>
      <div className="wa-header">
        <Icon name="lock" size={12} className="wa-lock" />
        <span>End-to-end encrypted</span>
      </div>

      <div className="phone-top">
        <div className="avatar-wrapper">
          <div className="avatar" aria-hidden="true">{initials(name)}</div>
          {phase === 'connected' && <span className="avatar-live-badge">LIVE</span>}
        </div>
        <div className="phone-id">
          <h1 className="contact-name">{name}</h1>
          {who.tagline && <p className="phone-subtitle">{who.tagline}</p>}
          <p className="phone-status">
            {phase === 'idle' && 'Tap Call to start'}
            {phase === 'ringing' && 'Ringing…'}
            {phase === 'connected' && formatClock(seconds)}
            {phase === 'reconnecting' && 'Reconnecting…'}
            {phase === 'ended' && `Call ended · ${formatClock(seconds)}`}
            {phase === 'error' && 'Call failed'}
          </p>
        </div>
      </div>

      <div className="phone-body">
        {live && (
          <>
            {paattiSpeaking ? (
              <div className="turn-indicator speaking" role="status">
                <span className="speaker-wave-pulse"><Icon name="volume" size={18} /></span>
                <div className="turn-text">
                  <strong>{name.split(' ').pop()} is speaking…</strong>
                  <span>Listen, then reply</span>
                </div>
              </div>
            ) : (
              <div className="turn-indicator your-turn" role="status">
                <span className="mic-dot pulse" aria-hidden="true"></span>
                <div className="turn-text">
                  <strong>{noMic ? 'Your turn: tap a line' : muted ? 'Mic muted' : 'Your turn: speak now'}</strong>
                  <span>{noMic ? `Tap step ${nextBeat + 1}, or type below` : `Read step ${nextBeat + 1} aloud, or tap it`}</span>
                </div>
              </div>
            )}

            {subtitle && (
              <div className={`live-speech-bubble ${paattiSpeaking ? 'active' : 'idle'}`}>
                <div className="speech-bubble-header">
                  <span className="bubble-speaker">{name}</span>
                  {paattiSpeaking && <span className="bubble-live-tag">Speaking</span>}
                </div>
                <p className="speech-bubble-content">“{subtitle}”</p>
              </div>
            )}
          </>
        )}
        {error && <p className="phone-error" role="alert">{error}</p>}

        {live && (
          <div className="phone-demo-sheet">
            <div className="demo-sheet-header">
              <span className="demo-sheet-title"><Icon name="script" size={14} /> Demo script</span>
              <span className="demo-badge">Read aloud or tap</span>
            </div>
            <div className="demo-cards-list">
              {DEMO_STEPS.map((s, idx) => {
                const isDone = idx < beatsSent
                const isCurrent = idx === nextBeat && !isDone
                return (
                  <button
                    type="button"
                    key={s.step}
                    className={`script-card ${isCurrent ? 'current' : ''} ${isDone ? 'done' : ''}`}
                    onClick={() => sendBeat(s.line)}
                    disabled={phase !== 'connected'}
                  >
                    <div className="script-card-header">
                      <span className="script-tag">{s.tag}</span>
                      <span className="script-target">{s.target}</span>
                    </div>
                    <p className="script-speech-text">“{s.line}”</p>
                    <span className="script-tap-hint">{isDone ? <><Icon name="check" size={12} /> Sent</> : 'Tap to send'}</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}
      </div>

      <div className="phone-bottom">
        {phase === 'connected' && (!voice || noMic) && (
          <form className="phone-type" onSubmit={sendDraft}>
            <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Type what you say…" aria-label="Your line" />
            <button aria-label="Send"><Icon name="send" size={18} /></button>
          </form>
        )}

        <div className="phone-actions">
          {(phase === 'idle' || phase === 'ended' || phase === 'error') && (
            <div className="action-col">
              <button className="round call pulse-call" onClick={call} aria-label="Call"><Icon name="phone" size={28} /></button>
              <span className="btn-label-primary">{phase === 'idle' ? 'Call' : 'Call again'}</span>
            </div>
          )}
          {(phase === 'ringing' || live) && (
            <>
              <div className="action-col">
                <button
                  className={`round ghost ${muted ? 'muted-on' : ''}`}
                  onClick={() => setMuted((m) => !m)}
                  aria-pressed={muted}
                  aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
                >
                  <Icon name={muted ? 'micOff' : 'mic'} size={24} />
                </button>
                <span className="btn-label">{muted ? 'Unmute' : 'Mute'}</span>
              </div>
              <div className="action-col">
                <button className="round hang" onClick={() => hangup()} aria-label="End call"><Icon name="phone" size={28} /></button>
                <span className="btn-label end-label">End</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
