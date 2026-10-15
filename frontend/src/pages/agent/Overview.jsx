import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, usePoll } from '../../api'

const when = (ts) => new Date(ts * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const STAGE = { planning: 'Planning', executing: 'Working', review: 'Finishing' }

function DiscussPanel({ a }) {
  const [items, reload] = usePoll(`/discuss?agent_id=${a.id}`, 6000, [a.id])
  const [ans, setAns] = useState({})
  const [msg, setMsg] = useState('')
  const guard = async (fn) => { setMsg(''); try { await fn(); reload() } catch (e) { setMsg(e.message) } }
  if (!(items || []).length) return null
  return (
    <section className="section">
      <h3>{a.name} wants to ask you</h3>
      <div className="list">
        {items.map((d) => (
          <div key={d.id} className="item" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div className="grow"><b style={{ whiteSpace: 'normal' }}>{d.topic}</b>{d.detail && <span className="muted small">{d.detail}</span>}</div>
            <div className="actions" style={{ width: '100%' }}>
              <input style={{ flex: 1 }} placeholder="Your answer" value={ans[d.id] || ''} onChange={(e) => setAns({ ...ans, [d.id]: e.target.value })} />
              <button className="btn small solid" disabled={!(ans[d.id] || '').trim()} onClick={() => guard(async () => { await api.post(`/discuss/${d.id}/answer`, { answer: ans[d.id], remember: true }); setAns({ ...ans, [d.id]: '' }) })}>Answer</button>
              <button className="link" onClick={() => guard(() => api.post(`/discuss/${d.id}/dismiss`))}>Dismiss</button>
            </div>
          </div>
        ))}
      </div>
      {msg && <p className="warn small">{msg}</p>}
    </section>
  )
}

function VideoPanel({ a }) {
  const [list, reload] = usePoll(`/agents/${a.id}/video`, 4000, [a.id])
  const [url, setUrl] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const join = async () => { setMsg(''); setBusy(true); try { await api.post(`/agents/${a.id}/video/join`, { url }); setUrl(''); reload() } catch (e) { setMsg(e.message) } setBusy(false) }
  const live = (list || []).filter((s) => s.status === 'joining' || s.status === 'in_call')
  return (
    <details className="section">
      <summary className="muted" style={{ cursor: 'pointer' }}>Join a video meeting (Meet, Zoom, Teams)</summary>
      <div className="form-grid" style={{ marginTop: 10 }}>
        <div className="actions"><input style={{ flex: 1 }} placeholder="Paste the meeting link" value={url} onChange={(e) => setUrl(e.target.value)} /><button className="btn solid" disabled={!url.trim() || busy} onClick={join}>{busy ? 'Joining…' : 'Join'}</button></div>
        <p className="small muted">{a.name} listens, takes notes and speaks only when spoken to. Needs the meeting bot under Apps.</p>
        {msg && <p className="warn small">{msg}</p>}
        {(list || []).map((s) => (
          <div key={s.id} className="result-box">
            <div className="actions" style={{ justifyContent: 'space-between' }}>
              <b className="small">{s.status === 'in_call' ? 'In the meeting' : s.status === 'joining' ? 'Joining…' : 'Ended'}</b>
              {live.some((x) => x.id === s.id) && <button className="link" onClick={async () => { await api.post(`/video/${s.id}/leave`); reload() }}>Leave</button>}
            </div>
            <p className="muted small">{s.url}</p>
            {s.error && <p className="warn small">{s.error}</p>}
            {s.minutes && <p className="small"><b>Minutes:</b> {s.minutes.summary}</p>}
          </div>
        ))}
      </div>
    </details>
  )
}

export default function Overview({ a, go }) {
  return (
    <div style={{ maxWidth: 760 }}>
      {a.personality && <p className="muted" style={{ marginBottom: 28 }}>{a.personality}</p>}

      <section className="section">
        <h3>To-do</h3>
        <div className="list">
          {a.tasks.length === 0 && <div className="empty-line">Nothing on their list.</div>}
          {a.tasks.map((t) => (
            <div key={t.id} className="item">
              <div className="grow">
                <b>{t.title}</b>
                {t.steps_total > 0 && t.status === 'doing' && <span className="stagebar" style={{ marginTop: 6, maxWidth: 200 }} title={`${t.steps_done} of ${t.steps_total} steps`}>{Array.from({ length: t.steps_total }, (_, i) => <i key={i} className={i < t.steps_done ? 'on' : i === t.steps_done ? 'cur' : ''} />)}</span>}
              </div>
              <div className="side-r">
                {t.due && <span>due {t.due}</span>}
                {t.status === 'doing' ? <span className={`tag ${t.stage === 'planning' ? 'plan' : 'doing'}`}>{STAGE[t.stage] || 'Working'}</span> : t.priority === 'high' ? <span className="tag">High</span> : null}
              </div>
            </div>
          ))}
        </div>
        <button className="link" style={{ padding: 0, marginTop: 8 }} onClick={() => go('computer')}>Open their to-do app</button>
      </section>

      <DiscussPanel a={a} />

      <section className="section">
        <h3>Recently</h3>
        <div className="list">
          {a.recent.length === 0 && <div className="empty-line">Nothing yet.</div>}
          {a.recent.map((e) => <div key={e.id} className={`item ${e.ok ? '' : 'bad'}`}><span className="grow" style={{ whiteSpace: 'normal', color: e.ok ? undefined : 'var(--bad)' }}>{e.summary}</span><span className="side-r">{when(e.ts)}</span></div>)}
        </div>
        <button className="link" style={{ padding: 0, marginTop: 8 }} onClick={() => go('history')}>Full history</button>
      </section>

      {a.projects.length > 0 && (
        <section className="section">
          <h3>Projects</h3>
          <div className="list">{a.projects.map((p) => <Link key={p.id} to={`/projects/${p.id}`} className="item"><span className="grow"><b>{p.name}</b></span></Link>)}</div>
        </section>
      )}

      <VideoPanel a={a} />
    </div>
  )
}
