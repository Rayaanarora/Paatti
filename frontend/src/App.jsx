import { useState } from 'react'
import { apiUrl } from './api.js'
import { Dispatch, LiveCalls, PhoneDemo } from './components/Ops.jsx'
import { IntelCards, StageTracker, TimeWasted, Transcript } from './components/Panels.jsx'
import { CHANNEL_LABEL } from './state.js'
import { useCall } from './useCall.js'
import { Icon } from './icons.jsx'

// The rehearsed demo script (docs/DEMO_SCRIPT.md); mirrors backend/paatti/demo.py CALLER_LINES.
// Paatti's replies to these are pre-recorded, so the demo plays instantly.
const DEMO_LINES = [
  'Hello, am I speaking with Kamala madam? This is Inspector Rajesh Verma from CBI, New Delhi.',
  'Madam, your Aadhaar card is linked to a money-laundering case. Sixty-eight bank accounts are opened in your name.',
  'An arrest warrant is issued against you. This is a digital arrest. Do not disconnect and do not tell anyone.',
  'To clear your name, transfer Rs 4,50,000 to the RBI safe account. Note the number: 5021 0034 7781 9920.',
  'Yes, correct. The IFSC is HDFC0001234.',
  'Correct. Transfer it immediately, the officer is waiting.',
  'Fine, send it by UPI to rbi.verify@ybl.',
  'Yes! Do it now, madam, quickly.',
]

export default function App() {
  const call = useCall()
  const { state, health } = call
  const [draft, setDraft] = useState('')
  const active = Boolean(state.sessionId) && !state.ended
  const drivable = active && state.channel === 'web'
  const serverVoice = health?.voice === 'sarvam'

  const submit = (e) => {
    e.preventDefault()
    call.sayAsScammer(draft)
    setDraft('')
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">P</span>
          <div>
            <h1>Paatti</h1>
            <p className="tagline">Scam-call honeypot · live console</p>
          </div>
        </div>
        <div className="status">
          {health && (
            <span className="modes" title="Backend modes">
              <span>Persona <b>{health.persona}</b></span>
              <span>Extractor <b>{health.extractor}</b></span>
              <span>Voice <b>{health.voice}</b></span>
            </span>
          )}
          <span className={`conn ${state.connected ? 'on' : ''}`}>
            <span className="dot" aria-hidden="true" />
            {state.connected ? 'Connected' : 'Offline'}
          </span>
        </div>
      </header>

      <main className="grid">
        <div className="col-left">
          <Transcript
            turns={state.turns}
            pendingReadback={state.pendingReadback}
            speaking={call.speaking}
            personaName={health?.personas?.find((p) => p.key === state.persona)?.name || 'Paatti'}
          />

          <section className="panel controls" aria-label="Call controls">
            <div className="row">
              {!active ? (
                <button className="btn primary" onClick={() => call.start()}>{state.ended ? 'New call' : 'Start call'}</button>
              ) : (
                <button className="btn danger" onClick={call.end}>End call</button>
              )}
              {active && state.channel !== 'web' && (
                <span className="pill">{CHANNEL_LABEL[state.channel]}{state.callerId ? ` · ${state.callerId}` : ''} · live</span>
              )}
              <div className="segmented" role="radiogroup" aria-label="Input mode">
                {[
                  ['text', 'Text'],
                  [serverVoice ? 'server' : 'browser', serverVoice ? 'Voice (Sarvam)' : 'Voice (browser)'],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    role="radio"
                    aria-checked={call.voiceMode === key}
                    className={call.voiceMode === key ? 'on' : ''}
                    onClick={() => { call.stopListening(); call.setVoiceMode(key) }}
                  >{label}</button>
                ))}
              </div>
              {call.voiceMode !== 'text' && (
                <label className="check">
                  <input type="checkbox" checked={call.muted} onChange={(e) => call.setMuted(e.target.checked)} /> Mute Paatti
                </label>
              )}
            </div>

            {call.voiceMode === 'browser' && drivable && (
              <button
                className={`btn mic ${call.listening ? 'live' : ''}`}
                onClick={call.listening ? call.stopListening : call.startListening}
              >{call.listening ? 'Listening… tap to stop' : 'Start microphone'}</button>
            )}
            {call.voiceMode === 'server' && drivable && (
              <button
                className={`btn mic ${call.listening ? 'live' : ''}`}
                onPointerDown={call.pttStart}
                onPointerUp={call.pttStop}
                onPointerLeave={() => call.listening && call.pttStop()}
              >{call.listening ? 'Release to send' : 'Hold to talk'}</button>
            )}

            <form className="say" onSubmit={submit}>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={active ? 'Type what the scammer says…' : 'Start a call first'}
                disabled={!active}
                maxLength={2000}
                aria-label="Scammer line"
              />
              <button className="btn" disabled={!active || !draft.trim()}>Send</button>
            </form>
            {drivable && (
              <details className="demo">
                <summary>Demo script lines</summary>
                <div className="demo-lines">
                  {DEMO_LINES.map((l) => (
                    <button key={l} className="chip" onClick={() => call.sayAsScammer(l)}>{l}</button>
                  ))}
                </div>
              </details>
            )}
          </section>
          <LiveCalls currentId={state.sessionId} currentLive={active} onOpen={call.attach} />
          <PhoneDemo health={health} onCreate={call.create} />
        </div>

        <div className="col-right">
          <TimeWasted state={state} />
          <StageTracker stage={state.stage} history={state.stageHistory} />
          <IntelCards intel={state.intel} />
          <section className="panel report" aria-label="Mule Intel Report">
            <header className="panel-head"><h2>Mule Intel Report</h2></header>
            <p className="muted">Identifiers with the caller's exact words, the stage timeline, BNS 318–319 and a SHA-256 hash. Digits the caller never confirmed stay out of the NCRP fields.</p>
            <Dispatch state={state} onDispatched={(m) => call.dispatch({ type: 'notice', id: Date.now(), message: m })} />
            <div className="row">
              <a className={`btn primary ${state.sessionId ? '' : 'disabled'}`} href={state.sessionId ? apiUrl(`/api/sessions/${state.sessionId}/report.pdf`) : undefined} aria-disabled={!state.sessionId}><Icon name="download" size={16} /> Download PDF</a>
              <a className={`btn ${state.sessionId ? '' : 'disabled'}`} href={state.sessionId ? apiUrl(`/api/sessions/${state.sessionId}/report.json`) : undefined} aria-disabled={!state.sessionId}>JSON</a>
            </div>
          </section>
        </div>
      </main>

      <div className="toasts" aria-live="assertive">
        {state.notices.map((n) => (
          <div key={n.id} className={`toast ${n.kind}`}>
            <span>{n.message}</span>
            <button aria-label="Dismiss" onClick={() => call.dispatch({ type: 'dismiss', id: n.id })}><Icon name="x" size={16} /></button>
          </div>
        ))}
      </div>
      <footer className="foot">Honeypot prototype. Own line only; no real person's voice or data.</footer>
    </div>
  )
}
