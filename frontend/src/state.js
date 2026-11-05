// Pure call-state reducer, kept free of React and the DOM so it can be unit tested.

export const STAGES = [
  { key: 'hook', label: 'Hook', hint: 'Pretext: parcel, case, Aadhaar' },
  { key: 'fear', label: 'Fear', hint: 'Arrest, warrant, jail' },
  { key: 'isolation', label: 'Isolation', hint: 'Stay on call, tell no one' },
  { key: 'extraction', label: 'Extraction', hint: 'Pay, transfer, account' },
]

export const INTEL_CARDS = [
  { key: 'claimed_agency', label: 'Claimed agency' },
  { key: 'amount', label: 'Amount demanded', format: (v) => formatInr(v) },
  { key: 'account_number', label: 'Account', readback: true, mono: true, format: (v) => groupDigits(v) },
  { key: 'ifsc', label: 'IFSC', readback: true, mono: true },
  { key: 'upi_id', label: 'UPI ID', readback: true, mono: true },
]

export const initialState = {
  sessionId: null,
  connected: false,
  turns: [],
  intel: {},
  stage: null,
  stageHistory: [],
  pendingReadback: null,
  ended: false,
  timeWasted: 0,
  timeSyncedAt: 0,
  notices: [],
  persona: 'paatti',
  channel: 'web',
  callerId: null,
  firstCaptureT: null,
  startedAt: null,
  dispatches: [],
}

export const GOLDEN_HOUR_S = 2 * 60 * 60

export function reducer(state, action) {
  switch (action.type) {
    case 'reset':
      return { ...initialState }
    case 'session': {
      return { ...initialState, ...applySnapshot(initialState, action.state, action.now), sessionId: action.state.session_id }
    }
    case 'connected':
      return { ...state, connected: action.value }
    case 'turn': {
      if (state.turns.some((t) => t.index === action.turn.index)) return state
      const turns = [...state.turns, action.turn].sort((a, b) => a.index - b.index)
      return { ...state, turns }
    }
    case 'state':
      return applySnapshot(state, action.state, action.now)
    case 'notice':
      return { ...state, notices: [...state.notices.slice(-3), { id: action.id, kind: action.kind || 'info', message: action.message }] }
    case 'dismiss':
      return { ...state, notices: state.notices.filter((n) => n.id !== action.id) }
    default:
      return state
  }
}

function applySnapshot(state, snap, now) {
  if (!snap) return state
  return {
    ...state,
    intel: snap.intel || {},
    stage: snap.stage,
    stageHistory: snap.stage_history || [],
    pendingReadback: snap.pending_readback,
    ended: Boolean(snap.ended),
    timeWasted: snap.time_wasted || 0,
    timeSyncedAt: now ?? Date.now(),
    persona: snap.persona || state.persona,
    channel: snap.channel || state.channel,
    callerId: snap.caller_id ?? state.callerId,
    firstCaptureT: snap.first_capture_t ?? null,
    startedAt: snap.started_at ?? state.startedAt,
    dispatches: snap.dispatches || [],
  }
}

/** Seconds wasted so far, ticking locally between server snapshots. */
export function liveSeconds(state, now = Date.now()) {
  if (!state.sessionId) return 0
  if (state.ended) return state.timeWasted
  return state.timeWasted + Math.max(0, (now - state.timeSyncedAt) / 1000)
}

export function formatClock(seconds) {
  const s = Math.floor(seconds)
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function formatInr(value) {
  const s = String(value).replace(/\D/g, '')
  if (!s) return value
  if (s.length <= 3) return `Rs ${s}`
  let head = s.slice(0, -3)
  const tail = s.slice(-3)
  const groups = []
  while (head.length > 2) {
    groups.unshift(head.slice(-2))
    head = head.slice(0, -2)
  }
  if (head) groups.unshift(head)
  return `Rs ${[...groups, tail].join(',')}`
}

export function groupDigits(value) {
  return String(value).replace(/(\d{4})(?=\d)/g, '$1 ')
}

/** Status of an intel card: empty, captured (unconfirmed), or locked. */
export function cardStatus(card, field) {
  if (!field) return 'empty'
  if (!card.readback) return 'locked'
  return field.confirmed ? 'locked' : 'captured'
}

/** Seconds left in the 2-hour recovery window since the first mule identifier was captured. */
export function goldenHourLeft(state, nowMs = Date.now()) {
  if (state.firstCaptureT == null || state.startedAt == null) return null
  const captured = state.startedAt + state.firstCaptureT
  return Math.max(0, GOLDEN_HOUR_S - (nowMs / 1000 - captured))
}

export function formatHms(seconds) {
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}:${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export const CHANNEL_LABEL = { web: 'Dashboard', 'web-phone': 'Phone (browser)', phone: 'Phone line' }

export function stageIndex(stage) {
  return STAGES.findIndex((s) => s.key === stage)
}
