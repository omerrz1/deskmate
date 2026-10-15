import { useEffect, useState } from 'react'
import { api } from '../../api'

const KINDS = [['all', 'Everything'], ['task', 'To-dos'], ['mail', 'Email'], ['call', 'Calls'], ['file', 'Files'], ['meeting', 'Meetings'], ['project', 'Projects'], ['training', 'Training'], ['profile', 'Profile changes'], ['manager', 'Manager actions'], ['tool', 'Other work']]

export default function History({ a }) {
  const [kind, setKind] = useState('all')
  const [q, setQ] = useState('')
  const [data, setData] = useState({ items: [], counts: {} })
  const [open, setOpen] = useState(null)
  const [loading, setLoading] = useState(false)
  const load = async (before = 0, append = false) => {
    setLoading(true)
    try {
      const r = await api.get(`/agents/${a.id}/history?kind=${kind}&q=${encodeURIComponent(q)}${before ? `&before=${before}` : ''}`)
      setData((d) => ({ counts: r.counts, items: append ? [...d.items, ...r.items] : r.items }))
    } catch { /* ignore */ }
    setLoading(false)
  }
  useEffect(() => { load() }, [a.id, kind]) // eslint-disable-line
  useEffect(() => { const t = setInterval(() => { if (!open) load() }, 8000); return () => clearInterval(t) }, [a.id, kind, q, open]) // eslint-disable-line

  const days = []
  data.items.forEach((e) => {
    const d = new Date(e.ts * 1000)
    const key = d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' })
    const last = days[days.length - 1]
    if (last && last.key === key) last.items.push(e); else days.push({ key, items: [e] })
  })
  const total = Object.values(data.counts).reduce((x, y) => x + y, 0)
  return (
    <div>
      <p className="muted small" style={{ marginBottom: 10 }}>Everything {a.name} has done, newest first. Click an entry to see the details. {total} entries so far.</p>
      <div className="chips">{KINDS.map(([k, n]) => <button key={k} className={`chip ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>{n}{k !== 'all' && data.counts[k] ? ` · ${data.counts[k]}` : ''}</button>)}</div>
      <div className="actions" style={{ margin: '12px 0' }}>
        <input style={{ flex: 1, maxWidth: 360 }} placeholder="Search the history" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load()} />
        <button className="btn" onClick={() => load()}>Search</button>
      </div>
      {days.length === 0 && !loading && <p className="muted">Nothing to show here yet.</p>}
      {days.map((d) => (
        <section key={d.key} className="hday">
          <h4>{d.key}</h4>
          {d.items.map((e) => (
            <div key={e.id} className={`hrow ${e.ok ? '' : 'bad'}`}>
              <button className="hmain" onClick={() => setOpen(open === e.id ? null : e.id)}>
                <time className="mono">{new Date(e.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
                <span className={`kind k-${e.kind}`}>{(KINDS.find(([k]) => k === e.kind) || [0, e.kind])[1]}</span>
                <span className="sum">{e.summary}{e.ok ? '' : ' (failed)'}</span>
              </button>
              {open === e.id && (
                <div className="hdetail">
                  {e.tool && <p className="small muted">Tool: <code>{e.tool}</code></p>}
                  {e.detail?.error && <p className="small warn">{e.detail.error}</p>}
                  {e.detail?.args && Object.keys(e.detail.args).length > 0 && <pre className="mailbody">{Object.entries(e.detail.args).map(([k, v]) => `${k}: ${v}`).join('\n')}</pre>}
                  {e.detail?.snippet && <p className="small">{e.detail.url} — {e.detail.snippet}</p>}
                  {e.detail && !e.detail.args && !e.detail.snippet && !e.detail.error && Object.keys(e.detail).length > 0 && <pre className="mailbody">{JSON.stringify(e.detail, null, 1)}</pre>}
                </div>
              )}
            </div>
          ))}
        </section>
      ))}
      {data.items.length >= 80 && <div className="actions"><button className="btn" disabled={loading} onClick={() => load(data.items[data.items.length - 1].id, true)}>Older</button></div>}
    </div>
  )
}
