import { useEffect, useRef, useState, useCallback } from 'react'
import { liveOk, onChange, topicsFor } from './live'

function csrf() {
  const m = document.cookie.match(/(?:^|; )deskmates_csrf=([^;]*)/)
  return m ? decodeURIComponent(m[1]) : ''
}
const unsafe = (m) => !['GET', 'HEAD', 'OPTIONS'].includes((m || 'GET').toUpperCase())

/** fetch() with the CSRF token attached to state-changing requests; signals the app when the session has ended. */
export async function apiFetch(url, init = {}) {
  const headers = { ...(init.headers || {}) }
  if (unsafe(init.method)) headers['X-CSRF-Token'] = csrf()
  const r = await fetch(url.startsWith('/api') ? url : '/api' + url, { credentials: 'same-origin', ...init, headers })
  if (r.status === 401) window.dispatchEvent(new Event('auth-expired'))
  if (r.status === 428) window.dispatchEvent(new Event('setup-needed'))  // the company has not connected its keys yet
  return r
}

async function handle(r) {
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail || msg } catch { /* ignore */ }
    throw new Error(typeof msg === 'string' ? msg : 'Request failed')
  }
  return r.json()
}

const send = (method) => (u, body) =>
  apiFetch(u, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) }).then(handle)

export const api = {
  get: (u) => apiFetch(u).then(handle),
  post: send('POST'),
  put: send('PUT'),
  patch: send('PATCH'),
  del: (u) => apiFetch(u, { method: 'DELETE' }).then(handle),
  form: (u, fd) => apiFetch(u, { method: 'POST', body: fd }).then(handle),
}

/** POST and consume a text/event-stream, calling onEvent for every JSON event. */
export async function stream(url, init, onEvent, signal) {
  const r = await apiFetch(url, { method: 'POST', signal, ...init })
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      if (chunk.startsWith('data: ')) {
        try { onEvent(JSON.parse(chunk.slice(6))) } catch { /* ignore */ }
      }
    }
  }
}

/** Load an endpoint and keep it fresh; returns [data, reload, error]. With ms > 0 the screen refreshes the moment the server says its tables changed
 * (see live.js), and the timer is only a safety net; with ms = 0 it loads once. */
export function usePoll(url, ms = 4000, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const alive = useRef(true)
  const loadedAt = useRef(0)
  const load = useCallback(async () => {
    try {
      const d = await api.get(url)
      loadedAt.current = Date.now()
      if (alive.current) { setData(d); setError(null) }
    } catch (e) { if (alive.current) setError(e) }
  }, [url])
  useEffect(() => {
    alive.current = true
    load()
    const topics = ms ? topicsFor(url) : []
    let deb
    const off = topics.length ? onChange((t) => { if (t.some((x) => topics.includes(x))) { clearTimeout(deb); deb = setTimeout(load, 120) } }) : null
    const timer = ms ? setInterval(() => { if (topics.length && liveOk() && Date.now() - loadedAt.current < 25000) return; load() }, ms) : null
    return () => { alive.current = false; clearTimeout(deb); if (timer) clearInterval(timer); if (off) off() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, ms, ...deps])
  return [data, load, error]
}

export const pulse = (level) => window.dispatchEvent(new CustomEvent('agent-pulse', { detail: level }))

export function ago(ts) {
  if (!ts) return '-'
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export const dur = (s) => {
  s = Math.round(s || 0)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
export const clock = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-')
export const label = (s) => (s ? String(s).replace(/_/g, ' ') : '-')
