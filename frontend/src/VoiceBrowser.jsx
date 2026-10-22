import { useEffect, useRef, useState } from 'react'
import { api } from './api'

// Plays one sample at a time; every Listen button in the page shares it.
let current = null
function stopSample() { if (current) { current.pause(); current = null } }

export function Listen({ url, label = 'Listen' }) {
  const [on, setOn] = useState(false)
  const audio = useRef(null)
  useEffect(() => () => { if (audio.current && current === audio.current) stopSample() }, [])
  if (!url) return null
  const toggle = () => {
    if (on) { stopSample(); setOn(false); return }
    stopSample()
    const a = new Audio(url)
    audio.current = a; current = a
    a.onended = () => setOn(false)
    a.onerror = () => setOn(false)
    a.play().then(() => setOn(true)).catch(() => setOn(false))
  }
  return <button type="button" className="link" onClick={toggle}>{on ? '■ Stop' : `▶ ${label}`}</button>
}

// The voice the call uses now: its name, what it sounds like, and a sample to play.
export function CurrentVoice({ voiceId, fallback = 'Not set' }) {
  const [info, setInfo] = useState(null)
  useEffect(() => {
    if (!voiceId) return undefined
    let alive = true
    api.get(`/voices/library/${voiceId}`).then((v) => { if (alive) setInfo({ id: voiceId, ...v }) }).catch(() => { if (alive) setInfo({ id: voiceId, missing: true }) })
    return () => { alive = false }
  }, [voiceId])
  if (!voiceId) return <span className="muted">{fallback}</span>
  if (info?.id !== voiceId) return <span className="muted">Loading the voice…</span>
  if (info.missing) return <span className="muted">This voice isn’t in your ElevenLabs account yet (ID {voiceId.slice(0, 6)}…)</span>
  return <span><b>{info.name}</b> <small className="muted">{[info.gender, info.accent, info.descriptive, info.use_case].filter(Boolean).join(' · ')}</small> <Listen url={info.preview_url} /></span>
}

// A searchable list of ElevenLabs voices: the ones already in the company's account, and ElevenLabs' public library.
export default function VoiceBrowser({ language = 'English', currentId = '', onPick }) {
  const [source, setSource] = useState('mine')
  const [form, setForm] = useState({ q: '', gender: '', accent: '' })
  const [query, setQuery] = useState({ source: 'mine', q: '', gender: '', accent: '' })
  const [result, setResult] = useState({ key: '', rows: [], err: '' })
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const key = JSON.stringify(query)

  useEffect(() => {
    let alive = true
    const qs = new URLSearchParams({ source: query.source, q: query.q, gender: query.gender, accent: query.accent, language: query.source === 'library' ? language : '' })
    api.get(`/voices/library?${qs}`)
      .then((rows) => { if (alive) setResult({ key, rows, err: '' }) })
      .catch((e) => { if (alive) setResult({ key, rows: [], err: e.message }) })
    return () => { alive = false }
  }, [key, language]) // eslint-disable-line

  const loading = result.key !== key
  const search = (e) => { e?.preventDefault(); setQuery({ source, ...form }) }
  const switchTo = (next) => { setSource(next); setQuery({ source: next, ...form }) }
  const choose = async (v) => {
    setBusy(v.voice_id); setErr('')
    try {
      // a public-library voice has to be in the company's own ElevenLabs account before a call can speak with it
      if (v.source === 'library' && v.public_owner_id) await api.post('/voices/add', { voice_id: v.voice_id, public_owner_id: v.public_owner_id, name: v.name })
      await onPick(v)
    } catch (e) { setErr(e.message) }
    setBusy('')
  }

  return (
    <div className="finder">
      <div className="chips" style={{ marginBottom: 10 }}>
        <button type="button" className={`chip ${source === 'mine' ? 'on' : ''}`} onClick={() => switchTo('mine')}>My ElevenLabs voices</button>
        <button type="button" className={`chip ${source === 'library' ? 'on' : ''}`} onClick={() => switchTo('library')}>ElevenLabs voice library</button>
      </div>
      <form className="filters" style={{ padding: 0, border: 0 }} onSubmit={search}>
        <input value={form.q} onChange={(e) => setForm({ ...form, q: e.target.value })} placeholder="Search by name or feel, e.g. warm, young, british" style={{ flex: 1, minWidth: 200 }} />
        <select value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })} aria-label="Voice gender"><option value="">Any voice</option><option value="female">Female</option><option value="male">Male</option></select>
        <input value={form.accent} onChange={(e) => setForm({ ...form, accent: e.target.value })} placeholder="Accent (optional)" style={{ maxWidth: 160 }} />
        <button className="btn" type="submit">Search</button>
      </form>
      {(err || result.err) && <p className="warn">{err || result.err}</p>}
      {loading && <p className="muted small">Loading voices…</p>}
      {!loading && !result.err && !result.rows.length && <p className="muted">{source === 'mine' ? 'No voices in your ElevenLabs account match. Try the voice library.' : 'No voices found. Try other words.'}</p>}
      {!loading && result.rows.map((v) => (
        <div key={v.voice_id} className="row-split">
          <div><b>{v.name}</b>{v.voice_id === currentId && <small className="muted"> · in use</small>}<small>{[v.gender, v.accent, v.age, v.descriptive, v.use_case].filter(Boolean).join(' · ')}</small></div>
          <span className="actions">
            <Listen url={v.preview_url} />
            <button type="button" className="btn small" disabled={!!busy || v.voice_id === currentId} onClick={() => choose(v)}>{busy === v.voice_id ? 'Adding…' : v.voice_id === currentId ? 'In use' : 'Use this voice'}</button>
          </span>
        </div>
      ))}
    </div>
  )
}
