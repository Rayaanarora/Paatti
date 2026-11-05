import { useEffect, useRef, useState } from 'react'
import { Icon } from '../icons.jsx'
import { INTEL_CARDS, STAGES, cardStatus, formatClock, liveSeconds, stageIndex } from '../state.js'

export function Transcript({ turns, pendingReadback, speaking, personaName = 'Paatti' }) {
  const end = useRef(null)
  useEffect(() => {
    // Block body on purpose: newer browsers return a Promise from scrollIntoView,
    // and React would treat a returned value as the effect cleanup.
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length])
  return (
    <section className="panel transcript" aria-label="Live transcript">
      <header className="panel-head">
        <h2>Live transcript</h2>
        {speaking && <span className="pill pill-speaking">{personaName} speaking</span>}
      </header>
      <div className="turns" role="log" aria-live="polite">
        {turns.length === 0 && (
          <div className="empty-hint">
            <p className="empty-title">No call on screen</p>
            <p>Start a call, then speak or type as the scammer. Phone calls appear here on their own.</p>
          </div>
        )}
        {turns.map((t) => (
          <div key={t.index} className={`bubble ${t.role}`}>
            <div className="who">
              {t.role === 'scammer' ? 'Scammer' : personaName}
              <span className="ts">{formatClock(t.t)}</span>
            </div>
            <p>{t.text}</p>
          </div>
        ))}
        {pendingReadback && (
          <div className="readback-hint">Waiting for the caller to confirm the {pendingReadback[0].replace('_', ' ')}</div>
        )}
        <div ref={end} />
      </div>
    </section>
  )
}

export function StageTracker({ stage, history }) {
  const current = stageIndex(stage)
  const at = Object.fromEntries(history.map((h) => [h.stage, h.t]))
  return (
    <section className="panel" aria-label="Scam stage">
      <header className="panel-head"><h2>Scam stage</h2></header>
      <ol className="stages">
        {STAGES.map((s, i) => {
          const status = i < current ? 'done' : i === current ? 'active' : 'todo'
          return (
            <li key={s.key} className={`stage ${status}`} aria-current={status === 'active' ? 'step' : undefined}>
              <span className="node" aria-hidden="true">{status === 'done' ? <Icon name="check" size={12} /> : i + 1}</span>
              <span className="stage-label">{s.label}</span>
              {at[s.key] !== undefined
                ? <span className="stage-hint at">at {formatClock(at[s.key])}</span>
                : <span className="stage-hint">{s.hint}</span>}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

const STATUS_TEXT = { empty: 'Waiting', captured: 'Read-back pending', locked: 'Captured' }

function LedgerRow({ label, value, mono, status, badge, meta, quote }) {
  return (
    <li className={`ledger-row ${status}`} title={quote ? `"${quote}"` : undefined}>
      <span className="ledger-label">{label}</span>
      <span className={`ledger-value ${mono ? 'mono' : ''}`}>{value || '—'}</span>
      <span className={`badge ${status}`}>
        {status === 'locked' && <Icon name="check" size={12} />}
        {badge}
      </span>
      {meta && <span className="ledger-meta">{meta}</span>}
    </li>
  )
}

export function IntelCards({ intel }) {
  const phones = intel.phone_numbers || []
  const apps = intel.apps_requested || []
  const listStatus = (xs) => (xs.length ? 'locked' : 'empty')
  return (
    <section className="panel" aria-label="Intel captured">
      <header className="panel-head">
        <h2>Intel captured</h2>
        <span className="panel-note">Hover a value for the caller's exact words</span>
      </header>
      <ul className="ledger">
        {INTEL_CARDS.map((c) => {
          const f = intel[c.key]
          const status = cardStatus(c, f)
          return (
            <LedgerRow
              key={c.key}
              label={c.label}
              value={f ? (c.format ? c.format(f.value) : f.value) : null}
              mono={c.mono}
              status={status}
              quote={f?.quote}
              badge={c.readback && f?.confirmed ? 'Read-back confirmed' : STATUS_TEXT[status]}
              meta={f && (
                <>
                  <span>{Math.round(f.confidence * 100)}% confidence</span>
                  <span>{f.source}</span>
                  {f.repeat_sessions?.length > 0 && <span className="repeat">seen in {f.repeat_sessions.length} earlier call(s)</span>}
                </>
              )}
            />
          )
        })}
        <LedgerRow label="Phone numbers" mono status={listStatus(phones)} value={phones.map((p) => p.value).join(', ')} badge={STATUS_TEXT[listStatus(phones)]} />
        <LedgerRow label="Apps requested" status={listStatus(apps)} value={apps.map((a) => a.value).join(', ')} badge={STATUS_TEXT[listStatus(apps)]} />
      </ul>
    </section>
  )
}

export function TimeWasted({ state }) {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!state.sessionId || state.ended) return undefined
    const id = setInterval(() => setTick((x) => x + 1), 500)
    return () => clearInterval(id)
  }, [state.sessionId, state.ended])
  return (
    <div className={`timer ${state.sessionId && !state.ended ? 'live' : ''}`} aria-label="Scammer time wasted">
      <div className="timer-label">
        <span className="timer-title">Scammer time wasted</span>
        <span className="timer-sub">{state.sessionId ? (state.ended ? 'Call ended' : 'Call in progress') : 'No call yet'}</span>
      </div>
      <div className="timer-value">{formatClock(liveSeconds(state))}</div>
    </div>
  )
}
