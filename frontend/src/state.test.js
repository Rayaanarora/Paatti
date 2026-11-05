import { describe, expect, it } from 'vitest'
import { cardStatus, formatClock, formatHms, formatInr, goldenHourLeft, groupDigits, initialState, liveSeconds, reducer, INTEL_CARDS } from './state.js'

const snap = (over = {}) => ({
  session_id: 'abc',
  time_wasted: 10,
  ended: false,
  stage: 'fear',
  stage_history: [{ stage: 'hook', t: 1, turn_index: 0 }],
  intel: {},
  pending_readback: null,
  ...over,
})

describe('reducer', () => {
  it('starts a session from a snapshot', () => {
    const s = reducer(initialState, { type: 'session', state: snap(), now: 1000 })
    expect(s.sessionId).toBe('abc')
    expect(s.stage).toBe('fear')
    expect(s.timeSyncedAt).toBe(1000)
  })

  it('appends turns in order and ignores duplicates', () => {
    let s = reducer(initialState, { type: 'turn', turn: { index: 1, role: 'paatti', text: 'b' } })
    s = reducer(s, { type: 'turn', turn: { index: 0, role: 'scammer', text: 'a' } })
    s = reducer(s, { type: 'turn', turn: { index: 0, role: 'scammer', text: 'a' } })
    expect(s.turns.map((t) => t.text)).toEqual(['a', 'b'])
  })

  it('keeps at most four notices', () => {
    let s = initialState
    for (let i = 0; i < 6; i++) s = reducer(s, { type: 'notice', id: i, message: String(i) })
    expect(s.notices.map((n) => n.id)).toEqual([2, 3, 4, 5])
    s = reducer(s, { type: 'dismiss', id: 3 })
    expect(s.notices.map((n) => n.id)).toEqual([2, 4, 5])
  })
})

describe('time wasted', () => {
  it('ticks locally until the call ends', () => {
    const s = reducer(initialState, { type: 'session', state: snap(), now: 1000 })
    expect(liveSeconds(s, 6000)).toBe(15)
    const ended = reducer(s, { type: 'state', state: snap({ ended: true, time_wasted: 42 }), now: 7000 })
    expect(liveSeconds(ended, 99999)).toBe(42)
  })

  it('formats as mm:ss', () => {
    expect(formatClock(872.4)).toBe('14:32')
  })
})

describe('formatting', () => {
  it('formats Indian rupee grouping', () => {
    expect(formatInr('450000')).toBe('Rs 4,50,000')
    expect(formatInr('12000000')).toBe('Rs 1,20,00,000')
    expect(formatInr('950')).toBe('Rs 950')
  })

  it('groups account digits by four', () => {
    expect(groupDigits('345678901234')).toBe('3456 7890 1234')
  })
})

describe('card status', () => {
  const account = INTEL_CARDS.find((c) => c.key === 'account_number')
  const agency = INTEL_CARDS.find((c) => c.key === 'claimed_agency')
  it('locks read-back fields only when confirmed', () => {
    expect(cardStatus(account, null)).toBe('empty')
    expect(cardStatus(account, { confirmed: false })).toBe('captured')
    expect(cardStatus(account, { confirmed: true })).toBe('locked')
    expect(cardStatus(agency, { confirmed: false })).toBe('locked')
  })
})

describe('golden hour', () => {
  it('counts down two hours from the first capture', () => {
    const s = reducer(initialState, { type: 'session', state: snap({ started_at: 1000, first_capture_t: 60, caller_id: '+91', channel: 'phone' }), now: 0 })
    expect(s.channel).toBe('phone')
    expect(goldenHourLeft(s, (1000 + 60 + 600) * 1000)).toBe(7200 - 600)
    expect(goldenHourLeft(initialState)).toBeNull()
    expect(formatHms(7199)).toBe('1:59:59')
  })
})
