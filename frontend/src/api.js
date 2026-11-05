// Where the backend lives. Empty = same origin (laptop, sandbox, Docker).
// On Vercel the dashboard is a static build and VITE_API_BASE points at the backend.
const BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

export const apiUrl = (path) => `${BASE}${path}`

export function wsUrl(path) {
  if (BASE) return BASE.replace(/^http/, 'ws') + path
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`
}
