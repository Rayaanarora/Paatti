import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { apiUrl } from '../api.js'
import { CHANNEL_LABEL, formatClock, formatHms, goldenHourLeft } from '../state.js'

/**
 * Live and recent calls. Auto-follow puts a phone call on this screen when it starts, and also
 * when this screen is idle (just opened, or showing an ended call) while a phone call is live,
 * so a laptop opened after the phone dialled still catches the call.
 */
export function LiveCalls({ currentId, currentLive = false, onOpen }) {
  const [calls, setCalls] = useState([])
  const [follow, setFollow] = useState(true)
  const seen = useRef(null)
  const current = useRef({ id: currentId, live: currentLive })
  current.current = { id: currentId, live: currentLive }

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const r = await fetch(apiUrl('/api/sessions'))
        if (!r.ok || !alive) return
        const list = await r.json()
        setCalls(list)
        const ids = new Set(list.map((c) => c.session_id))
        if (follow) {
          const cur = current.current
          const fresh = list.find((c) => c.channel !== 'web' && !c.ended && c.session_id !== cur.id
            && (!cur.live || (seen.current && !seen.current.has(c.session_id))))
          if (fresh) onOpen(fresh.session_id)
        }
        seen.current = ids
      } catch { /* backend restarting; try again next tick */ }
    }
    load()
    const id = setInterval(load, 2000)
    return () => { alive = false; clearInterval(id) }
  }, [follow, onOpen])

  return (
    <section className="panel" aria-label="Live calls">
      <header className="panel-head">
        <h2>Calls</h2>
        <label className="check"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Auto-follow phone calls</label>
      </header>
      {calls.length === 0 ? <p className="muted">No calls yet.</p> : (
        <ul className="call-list">
          {calls.slice(0, 6).map((c) => (
            <li key={c.session_id}>
              <button className={`call-row ${c.session_id === currentId ? 'on' : ''}`} onClick={() => onOpen(c.session_id)}>
                <span className={`live-dot ${c.ended ? '' : 'on'}`} aria-label={c.ended ? 'ended' : 'live'} />
                <span className="call-main">
                  <strong>{CHANNEL_LABEL[c.channel] || c.channel}</strong>
                  <span className="muted small">{c.caller_id || c.session_id} · {c.persona}</span>
                </span>
                <span className="call-meta">{c.stage || '—'} · {formatClock(c.time_wasted)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** QR for a browser phone call, and "ring this phone" over Twilio when configured. */
export function PhoneDemo({ health, onCreate }) {
  const [qr, setQr] = useState(null)
  const [link, setLink] = useState('')
  const [persona, setPersona] = useState(health?.default_persona || 'paatti')
  const [to, setTo] = useState('')
  const [token, setToken] = useState(() => { try { return sessionStorage.getItem('opToken') || '' } catch { return '' } })
  const [ringMsg, setRingMsg] = useState('')

  const makeQr = async () => {
    const snap = await onCreate({ channel: 'web-phone', persona })
    if (!snap) return
    const base = health?.public_url || window.location.origin
    const url = `${base}/?phone=${snap.session_id}`
    setLink(url)
    setQr(await QRCode.toDataURL(url, { margin: 1, width: 220 }))
  }

  const ring = async (e) => {
    e.preventDefault()
    try { sessionStorage.setItem('opToken', token) } catch { /* private mode */ }
    setRingMsg('Ringing…')
    try {
      const r = await fetch(apiUrl('/api/telephony/call'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Operator-Token': token },
        body: JSON.stringify({ to, persona }),
      })
      const body = await r.json()
      setRingMsg(r.ok ? `Calling ${body.to}… pick up and play the scammer.` : `Could not call: ${body.detail}`)
    } catch {
      setRingMsg('Could not reach the backend.')
    }
  }

  return (
    <section className="panel" aria-label="Phone demo">
      <header className="panel-head"><h2>Phone demo</h2></header>
      <div className="row">
        <label className="check">Persona
          <select value={persona} onChange={(e) => setPersona(e.target.value)}>
            <option value="random">Random (new grandparent every call)</option>
            {(health?.personas || [{ key: 'paatti', name: 'Kamala Paatti' }]).map((p) => (
              <option key={p.key} value={p.key}>{p.name}{p.tagline ? ` · ${p.tagline.split(' · ').pop()}` : ''}</option>
            ))}
          </select>
        </label>
        <button className="btn" onClick={makeQr}>Phone call via QR</button>
      </div>
      {qr && (
        <div className="qr">
          <img src={qr} alt="QR code to start the phone call" width="160" height="160" />
          <div>
            <p className="muted small">Scan with the judge's phone. The call screen opens; tap Call and play the scammer.</p>
            <a className="small" href={link} target="_blank" rel="noreferrer">{link}</a>
            {/^https?:\/\/(localhost|127\.|0\.0\.0\.0|\[::1\])/.test(link) ? (
              <p className="small warn-text" role="alert">A phone can't open a localhost link. Start Paatti with start.bat / start.sh (it makes a public https link), or open this dashboard through your tunnel URL.</p>
            ) : link.startsWith('http://') ? (
              <p className="small warn-text" role="alert">This is an http link: the phone's mic needs https. The call still works by tapping the lines.</p>
            ) : null}
          </div>
        </div>
      )}
      {health?.telephony ? (
        <form className="ring" onSubmit={ring}>
          <p className="muted small">Real call: Paatti's line {health.twilio_number} rings an allowlisted phone.</p>
          <div className="say">
            <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="Phone number" aria-label="Phone number to ring" inputMode="tel" />
            <input value={token} onChange={(e) => setToken(e.target.value)} placeholder="Operator token" aria-label="Operator token" type="password" />
            <button className="btn primary" disabled={!to || !token}>Ring</button>
          </div>
          {ringMsg && <p className="small">{ringMsg}</p>}
          <p className="muted small">Or dial {health.twilio_number} from any phone.</p>
        </form>
      ) : (
        <p className="muted small">Real phone line: set the Twilio variables (see docs/DEPLOY.md) to ring a phone or take inbound calls.</p>
      )}
    </section>
  )
}

/** Mock hand-off of the report to a bank fraud desk / 1930, with the golden-hour clock. */
export function Dispatch({ state, onDispatched }) {
  const [, tick] = useState(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const id = setInterval(() => tick((x) => x + 1), 1000)
    return () => clearInterval(id)
  }, [])
  const left = goldenHourLeft(state)
  const send = async (destination) => {
    setBusy(true)
    try {
      const r = await fetch(apiUrl(`/api/sessions/${state.sessionId}/dispatch`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ destination }),
      })
      const body = await r.json()
      onDispatched(r.ok ? `Sent to ${body.label} · ref ${body.reference} (mock)` : `Dispatch failed: ${body.detail}`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="dispatch">
      <div className="golden">
        <span className="muted small">Golden hour</span>
        <strong className="mono">{left == null ? 'starts at first account/UPI' : `${formatHms(left)} left`}</strong>
      </div>
      <div className="row">
        <button className="btn" disabled={!state.sessionId || busy} onClick={() => send('bank_fraud_desk')}>Send to bank fraud desk</button>
        <button className="btn" disabled={!state.sessionId || busy} onClick={() => send('ncrp_1930')}>Report to 1930 / NCRP</button>
      </div>
      {state.dispatches.length > 0 && (
        <ul className="dispatch-log">
          {state.dispatches.map((d) => (
            <li key={d.reference}><span className="mono">{d.reference}</span> {d.label} · {d.within_golden_hour ? 'within golden hour' : 'outside window'}</li>
          ))}
        </ul>
      )}
      <p className="muted small">Mock hand-off for the prototype: records the NCRP-ready fields that would be sent; contacts no one.</p>
    </div>
  )
}
