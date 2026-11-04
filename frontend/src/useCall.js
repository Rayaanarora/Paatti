import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { initialState, reducer } from './state.js'

const wsUrl = (id) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/sessions/${id}`

const SpeechRecognition = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition)

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

function pickVoice() {
  const voices = window.speechSynthesis?.getVoices() || []
  return voices.find((v) => v.lang === 'ta-IN') || voices.find((v) => v.lang === 'en-IN') || null
}

/**
 * Owns the live call: session, WebSocket, and the voice loop.
 * voiceMode: 'text' | 'browser' (Web Speech API) | 'server' (Sarvam via backend)
 */
export function useCall() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const [health, setHealth] = useState(null)
  const [listening, setListening] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [voiceMode, setVoiceMode] = useState('text')
  const [muted, setMuted] = useState(false)
  const ws = useRef(null)
  const recognizer = useRef(null)
  const recorder = useRef(null)
  const stream = useRef(null)
  const noticeId = useRef(0)
  const live = useRef({ listening: false, voiceMode: 'text', muted: false })
  live.current = { listening, voiceMode, muted }

  const notice = useCallback((message, kind = 'info') => {
    dispatch({ type: 'notice', id: ++noticeId.current, message, kind })
  }, [])

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => notice('Backend not reachable. Start it with: uvicorn paatti.main:app', 'error'))
  }, [notice])

  const send = useCallback((msg) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg))
  }, [])

  const speak = useCallback((text) => {
    if (live.current.muted || live.current.voiceMode === 'text' || !window.speechSynthesis) return
    const u = new SpeechSynthesisUtterance(text)
    const voice = pickVoice()
    if (voice) u.voice = voice
    u.lang = voice?.lang || 'en-IN'
    u.rate = 0.82
    u.pitch = 0.85
    pauseRecognition()
    setSpeaking(true)
    u.onend = u.onerror = () => {
      setSpeaking(false)
      resumeRecognition()
    }
    window.speechSynthesis.speak(u)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const playWav = useCallback((b64) => {
    if (live.current.muted) return
    const audio = new Audio(`data:audio/wav;base64,${b64}`)
    pauseRecognition()
    setSpeaking(true)
    audio.onended = audio.onerror = () => {
      setSpeaking(false)
      resumeRecognition()
    }
    audio.play().catch(() => setSpeaking(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const start = useCallback(async () => {
    ws.current?.close()
    dispatch({ type: 'reset' })
    let snap
    try {
      const r = await fetch('/api/sessions', { method: 'POST' })
      if (!r.ok) throw new Error(r.status === 429 ? 'Too many calls started; wait a minute.' : `HTTP ${r.status}`)
      snap = await r.json()
    } catch (e) {
      notice(`Could not start call: ${e.message}`, 'error')
      return
    }
    dispatch({ type: 'session', state: snap })
    const sock = new WebSocket(wsUrl(snap.session_id))
    ws.current = sock
    sock.onopen = () => dispatch({ type: 'connected', value: true })
    sock.onclose = () => dispatch({ type: 'connected', value: false })
    sock.onerror = () => notice('Live connection failed. Is the backend running with uvicorn[standard]?', 'error')
    sock.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'turn') {
        dispatch({ type: 'turn', turn: msg.turn })
        if (msg.turn.role === 'paatti' && live.current.voiceMode === 'browser') speak(msg.turn.text)
      } else if (msg.type === 'state') dispatch({ type: 'state', state: msg.state })
      else if (msg.type === 'paatti_audio') playWav(msg.data)
      else if (msg.type === 'notice') notice(msg.message, 'warn')
      else if (msg.type === 'error') notice(msg.message, 'error')
    }
  }, [notice, speak, playWav])

  const sayAsScammer = useCallback((text) => {
    const t = text.trim()
    if (t) send({ type: 'scammer_text', text: t })
  }, [send])

  // ---- browser voice: Web Speech recognition + mic recording for the audio hash
  function pauseRecognition() {
    try { recognizer.current?.stop() } catch { /* not running */ }
  }
  function resumeRecognition() {
    if (live.current.listening && live.current.voiceMode === 'browser') {
      try { recognizer.current?.start() } catch { /* already running */ }
    }
  }

  const startMic = useCallback(async () => {
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      notice('Microphone permission denied. Use text mode.', 'error')
      return false
    }
    return true
  }, [notice])

  const stopMic = useCallback(() => {
    recorder.current?.state === 'recording' && recorder.current.stop()
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
  }, [])

  const startListening = useCallback(async () => {
    if (voiceMode !== 'browser') return
    if (!SpeechRecognition) {
      notice('This browser has no speech recognition. Use Chrome, or text mode.', 'error')
      return
    }
    if (!(await startMic())) return
    // Raw caller audio goes to the server only to be hashed (chain of custody).
    const rec = new MediaRecorder(stream.current)
    rec.ondataavailable = async (e) => {
      if (e.data.size) send({ type: 'audio_chunk', data: toBase64(await e.data.arrayBuffer()) })
    }
    rec.start(1000)
    recorder.current = rec

    const r = new SpeechRecognition()
    r.lang = 'en-IN'
    r.continuous = true
    r.interimResults = false
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) sayAsScammer(e.results[i][0].transcript)
      }
    }
    r.onend = () => {
      if (live.current.listening && !window.speechSynthesis?.speaking) {
        try { r.start() } catch { /* already running */ }
      }
    }
    recognizer.current = r
    r.start()
    setListening(true)
  }, [voiceMode, notice, send, sayAsScammer, startMic])

  const stopListening = useCallback(() => {
    setListening(false)
    live.current.listening = false
    pauseRecognition()
    stopMic()
  }, [stopMic])

  // ---- server voice: push-to-talk, utterance sent to Sarvam via the backend
  const pttStart = useCallback(async () => {
    if (!(await startMic())) return
    const chunks = []
    const rec = new MediaRecorder(stream.current)
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data)
    rec.onstop = async () => {
      const blob = new Blob(chunks, { type: rec.mimeType })
      if (blob.size) send({ type: 'audio_utterance', data: toBase64(await blob.arrayBuffer()), mime: rec.mimeType })
      stream.current?.getTracks().forEach((t) => t.stop())
    }
    rec.start()
    recorder.current = rec
    setListening(true)
  }, [send, startMic])

  const pttStop = useCallback(() => {
    setListening(false)
    if (recorder.current?.state === 'recording') recorder.current.stop()
  }, [])

  const end = useCallback(() => {
    stopListening()
    window.speechSynthesis?.cancel()
    send({ type: 'end' })
  }, [send, stopListening])

  useEffect(() => () => {
    ws.current?.close()
    stopMic()
  }, [stopMic])

  return {
    state, dispatch, health, listening, speaking, voiceMode, setVoiceMode, muted, setMuted,
    start, end, sayAsScammer, startListening, stopListening, pttStart, pttStop,
    speechSupported: Boolean(SpeechRecognition),
  }
}
