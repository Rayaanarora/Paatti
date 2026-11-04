import { useState } from 'react'
import { IntelCards, StageTracker, TimeWasted, Transcript } from './components/Panels.jsx'
import { useCall } from './useCall.js'

// One-click lines for rehearsing the demo without a second person.
const DEMO_LINES = [
  'This is Inspector Rajesh Verma from CBI. Your Aadhaar is linked to a money-laundering case.',
  'An arrest warrant is issued in your name. This is a digital arrest, you cannot disconnect.',
  'Do not tell anyone in your family. Stay on this Skype call with camera on.',
  'Transfer Rs 4,50,000 to the RBI safe account now. Note the number: 5021 0034 7781 9920.',
  'Yes correct. IFSC is HDFC0001234.',
  'Haan, correct. Or pay by UPI to rbi.verify@ybl',
]

export default function App() {
  const call = useCall()
  const { state, health } = call
  const [draft, setDraft] = useState('')
  const active = Boolean(state.sessionId) && !state.ended
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
            <p className="tagline">Live call console</p>
          </div>
        </div>
        <div className="status">
          {health && (
            <span className="pill" title="Backend modes">
              persona: {health.persona} · extractor: {health.extractor} · voice: {health.voice}
            </span>
          )}
          <span className={`dot ${state.connected ? 'on' : ''}`} aria-label={state.connected ? 'Connected' : 'Not connected'} />
        </div>
      </header>

      <main className="grid">
        <div className="col-left">
          <Transcript turns={state.turns} pendingReadback={state.pendingReadback} speaking={call.speaking} />

          <section className="panel controls" aria-label="Call controls">
            <div className="row">
              {!active ? (
                <button className="btn primary" onClick={call.start}>{state.ended ? 'New call' : 'Start call'}</button>
              ) : (
                <button className="btn danger" onClick={call.end}>End call</button>
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

            {call.voiceMode === 'browser' && active && (
              <button
                className={`btn mic ${call.listening ? 'live' : ''}`}
                onClick={call.listening ? call.stopListening : call.startListening}
              >{call.listening ? 'Listening… tap to stop' : 'Start microphone'}</button>
            )}
            {call.voiceMode === 'server' && active && (
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
            {active && (
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
        </div>

        <div className="col-right">
          <TimeWasted state={state} />
          <StageTracker stage={state.stage} history={state.stageHistory} />
          <IntelCards intel={state.intel} />
          <section className="panel report" aria-label="Mule Intel Report">
            <header className="panel-head"><h2>Mule Intel Report</h2></header>
            <p className="muted">Identifiers, verbatim evidence quotes, stage timeline, BNS 318–319, SHA-256 hash. Unconfirmed digits stay out of the NCRP fields.</p>
            <div className="row">
              <a className={`btn primary ${state.sessionId ? '' : 'disabled'}`} href={state.sessionId ? `/api/sessions/${state.sessionId}/report.pdf` : undefined} aria-disabled={!state.sessionId}>Download PDF</a>
              <a className={`btn ${state.sessionId ? '' : 'disabled'}`} href={state.sessionId ? `/api/sessions/${state.sessionId}/report.json` : undefined} aria-disabled={!state.sessionId}>JSON</a>
            </div>
          </section>
        </div>
      </main>

      <div className="toasts" aria-live="assertive">
        {state.notices.map((n) => (
          <div key={n.id} className={`toast ${n.kind}`}>
            <span>{n.message}</span>
            <button aria-label="Dismiss" onClick={() => call.dispatch({ type: 'dismiss', id: n.id })}>×</button>
          </div>
        ))}
      </div>
      <footer className="foot">Honeypot prototype. Own line only; no real person's voice or data.</footer>
    </div>
  )
}
