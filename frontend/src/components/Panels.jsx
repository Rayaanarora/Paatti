import { useEffect, useRef, useState } from 'react'
import { INTEL_CARDS, STAGES, cardStatus, formatClock, liveSeconds, stageIndex } from '../state.js'

export function Transcript({ turns, pendingReadback, speaking }) {
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
        {speaking && <span className="pill pill-speaking">Paatti speaking</span>}
      </header>
      <div className="turns" role="log" aria-live="polite">
        {turns.length === 0 && (
          <p className="empty-hint">Start a call, then speak or type as the scammer. Paatti answers in her own voice.</p>
        )}
        {turns.map((t) => (
          <div key={t.index} className={`bubble ${t.role}`}>
            <div className="who">
              {t.role === 'scammer' ? 'Scammer' : 'Paatti'}
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
            <li key={s.key} className={`stage ${status}`}>
              <span className="num">{i + 1}</span>
              <div>
                <div className="stage-label">{s.label}</div>
                <div className="stage-hint">{at[s.key] !== undefined ? `reached ${formatClock(at[s.key])}` : s.hint}</div>
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

const STATUS_TEXT = { empty: 'Waiting', captured: 'Read-back pending', locked: 'Locked' }

export function IntelCards({ intel }) {
  const phones = intel.phone_numbers || []
  const apps = intel.apps_requested || []
  return (
    <section className="panel" aria-label="Intel captured">
      <header className="panel-head"><h2>Intel captured</h2></header>
      <div className="cards">
        {INTEL_CARDS.map((c) => {
          const f = intel[c.key]
          const status = cardStatus(c, f)
          return (
            <article key={c.key} className={`card ${status}`} title={f ? `"${f.quote}"` : undefined}>
              <div className="card-top">
                <span className="card-label">{c.label}</span>
                <span className={`badge ${status}`}>{c.readback && f?.confirmed ? 'Read-back confirmed' : STATUS_TEXT[status]}</span>
              </div>
              <div className={`card-value ${c.mono ? 'mono' : ''}`}>{f ? (c.format ? c.format(f.value) : f.value) : '—'}</div>
              {f && (
                <div className="card-meta">
                  <span>conf {Math.round(f.confidence * 100)}%</span>
                  <span>{f.source}</span>
                  {f.repeat_sessions?.length > 0 && <span className="repeat">seen in {f.repeat_sessions.length} earlier call(s)</span>}
                </div>
              )}
            </article>
          )
        })}
        <article className={`card ${phones.length ? 'locked' : 'empty'}`}>
          <div className="card-top"><span className="card-label">Phone numbers</span></div>
          <div className="card-value mono small">{phones.length ? phones.map((p) => p.value).join(', ') : '—'}</div>
        </article>
        <article className={`card ${apps.length ? 'locked' : 'empty'}`}>
          <div className="card-top"><span className="card-label">Apps requested</span></div>
          <div className="card-value small">{apps.length ? apps.map((a) => a.value).join(', ') : '—'}</div>
        </article>
      </div>
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
    <div className="timer" aria-label="Scammer time wasted">
      <div className="timer-value">{formatClock(liveSeconds(state))}</div>
      <div className="timer-label">scammer time wasted</div>
    </div>
  )
}
