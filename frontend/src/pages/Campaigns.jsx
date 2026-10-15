import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, usePoll, label } from '../api'
import { Panel, Badge, Empty } from '../ui'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const TEMPLATES = [
  ['Follow up on an enquiry', 'Follow up on their enquiry: confirm they are still interested, answer their question briefly, and agree the next step.'],
  ['Re-engage a quiet lead', 'They showed interest but went quiet. Find out what stopped them and help, or book a call with the right person.'],
  ['Book a meeting or demo', 'Invite them to a meeting or demo and agree a date and time.'],
  ['Collect missing information', 'Politely ask them to send the information we are still missing and offer help if they are stuck.'],
  ['Confirm next steps', 'Confirm they are ready to go ahead and that they have everything they need.'],
]

function Form({ initial, onDone }) {
  const [f, setF] = useState({ name: '', objective: '', instructions: '', max_attempts: 3, retry_delay_min: 1, voice: '', kb_id: '', agent_id: '', ...initial.form })
  const [kbs] = usePoll('/kb', 0)
  const [team] = usePoll('/agents', 0)
  useEffect(() => { if ((kbs || []).length === 1 && !f.kb_id) setF((x) => ({ ...x, kb_id: String(kbs[0].id) })) }, [kbs]) // eslint-disable-line
  const [when, setWhen] = useState({ later: false, at: '' })
  const [win, setWin] = useState({ on: false, start: '09:00', end: '20:00', days: [0, 1, 2, 3, 4] })
  const [fu, setFu] = useState({ callbacks: true, nudge_days: 2, nudge_max: 1 })
  const [cats, setCats] = useState(null)          // null = all knowledge
  const [catCounts, setCatCounts] = useState([])
  const [auto, setAuto] = useState(false)
  useEffect(() => {
    setCats(null); setCatCounts([])
    if (f.kb_id) api.get(`/kb/${f.kb_id}`).then((k) => { const m = {}; k.docs.forEach((d) => { m[d.category] = (m[d.category] || 0) + 1 }); setCatCounts(Object.entries(m)) }).catch(() => {})
  }, [f.kb_id]) // eslint-disable-line
  const toggleCat = (c) => setCats((cur) => { const all = catCounts.map(([k]) => k); const base = cur || all; const n = base.includes(c) ? base.filter((x) => x !== c) : [...base, c]; return n.length === all.length ? null : n })
  const [filters, setFilters] = useState(initial.filters || {})
  const [segs] = usePoll('/segments', 0)
  const [aud, setAud] = useState(null)
  useEffect(() => { api.post('/segment/count', { filters }).then(setAud).catch(() => {}) }, [filters])
  const sf = (k, v) => setFilters((x) => { const n = { ...x }; if (v) n[k] = v; else delete n[k]; return n })
  const create = async (start) => {
    const startAt = when.later && when.at ? new Date(when.at).getTime() / 1000 : null
    const c = await api.post('/campaigns', {
      ...f, voice: f.voice || null, kb_id: f.kb_id ? +f.kb_id : null, agent_id: f.agent_id ? +f.agent_id : null, filters, start_at: startAt,
      call_window: win.on ? { start: win.start, end: win.end, days: win.days } : {}, followup: fu, kb_categories: cats || [],
    })
    if (start) await api.post(`/campaigns/${c.id}/start`)
    onDone()
  }
  return (
    <Panel title="New campaign" right={<button className="link" onClick={onDone}>Close</button>}>
      <div className="chips">{TEMPLATES.map(([n, o]) => <button key={n} className="chip" onClick={() => setF((x) => ({ ...x, name: x.name || n, objective: o }))}>{n}</button>)}</div>
      <div className="form">
        <label>Who makes the calls? <span className="faint">(their persona and voice are used on the call)</span>
          <select value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })}>
            <option value="">Default</option>{(team || []).map((a) => <option key={a.id} value={a.id}>{a.name} · {a.role}</option>)}
          </select>
        </label>
        <label>Knowledge base <span className="faint">(persona, context and facts the agent uses)</span>
          <select value={f.kb_id} onChange={(e) => setF({ ...f, kb_id: e.target.value })}>
            <option value="">None yet</option>{(kbs || []).map((k) => <option key={k.id} value={k.id}>{k.name} ({k.docs} documents)</option>)}
          </select>
          <Link className="link" to="/knowledge">Manage knowledge bases</Link>
        </label>
        {catCounts.length > 1 && (
          <div><p className="small muted">Which knowledge should this campaign use? <span className="faint">(tap to turn a section off)</span></p>
            <div className="chips" style={{ marginTop: 8 }}>{catCounts.map(([c, n]) => <button key={c} className={`chip ${(cats || catCounts.map(([k]) => k)).includes(c) ? 'on' : ''}`} onClick={() => toggleCat(c)}>{c} · {n}</button>)}</div></div>
        )}
        <label>Campaign name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="September applicants" /></label>
        <label>What should the agent achieve?<textarea rows="3" value={f.objective} onChange={(e) => setF({ ...f, objective: e.target.value })} /></label>
        <div className="form-row">
          <label>Who to call (language)<select value={filters.language || ''} onChange={(e) => sf('language', e.target.value)}><option value="">Everyone</option>{(segs?.language || []).map((o) => <option key={o.v} value={o.v}>{o.v} ({o.n})</option>)}</select></label>
          <label>Group<select value={filters.tags || ''} onChange={(e) => sf('tags', e.target.value)}><option value="">All</option>{(segs?.tags || []).map((o) => <option key={o.v} value={o.v}>{o.v} ({o.n})</option>)}</select></label>
        </div>
        <label>Extra context for the agent <span className="faint">(optional: offers, deadlines, anything special)</span><textarea rows="2" value={f.instructions} onChange={(e) => setF({ ...f, instructions: e.target.value })} placeholder="e.g. September intake closes on 15 Aug. Mention the early-bird bursary." /></label>
        <div className="sect">
          <h5>When to call</h5>
          <div className="chips">
            <button className={`chip ${!when.later ? 'on' : ''}`} onClick={() => setWhen({ ...when, later: false })}>Start now</button>
            <button className={`chip ${when.later ? 'on' : ''}`} onClick={() => setWhen({ ...when, later: true })}>Schedule a start</button>
            {when.later && <input type="datetime-local" style={{ width: 'auto' }} value={when.at} onChange={(e) => setWhen({ ...when, at: e.target.value })} />}
          </div>
          <div className="chips">
            <button className={`chip ${win.on ? 'on' : ''}`} onClick={() => setWin({ ...win, on: !win.on })}>Only call during set hours</button>
            {win.on && <>
              <input type="time" style={{ width: 'auto' }} value={win.start} onChange={(e) => setWin({ ...win, start: e.target.value })} /> to
              <input type="time" style={{ width: 'auto' }} value={win.end} onChange={(e) => setWin({ ...win, end: e.target.value })} />
              {DAYS.map((d, i) => <button key={d} className={`chip ${win.days.includes(i) ? 'on' : ''}`} onClick={() => setWin({ ...win, days: win.days.includes(i) ? win.days.filter((x) => x !== i) : [...win.days, i] })}>{d}</button>)}
            </>}
          </div>
        </div>
        <div className="sect">
          <h5>Automatic follow-up</h5>
          <div className="form-row">
            <label>Retry unanswered, up to<input type="number" min="1" max="10" value={f.max_attempts} onChange={(e) => setF({ ...f, max_attempts: +e.target.value })} /></label>
            <label>Wait before retry (minutes)<input type="number" min="0" value={f.retry_delay_min} onChange={(e) => setF({ ...f, retry_delay_min: +e.target.value })} /></label>
            <label>Follow up interested people after (days)<input type="number" min="1" value={fu.nudge_days} onChange={(e) => setFu({ ...fu, nudge_days: +e.target.value })} /></label>
            <label>Follow-up calls, at most<input type="number" min="0" max="5" value={fu.nudge_max} onChange={(e) => setFu({ ...fu, nudge_max: +e.target.value })} /></label>
          </div>
          <label className="check"><input type="checkbox" checked={fu.callbacks} onChange={(e) => setFu({ ...fu, callbacks: e.target.checked })} /> Call back at the time the person asks for</label>
        </div>
        <details className="adv"><summary>More options</summary>
          <div className="form-row">
            <label>Status<select value={filters.status || ''} onChange={(e) => sf('status', e.target.value)}><option value="">any</option>{(segs?.status || []).map((o) => <option key={o.v} value={o.v}>{label(o.v)} ({o.n})</option>)}</select></label>
            <label>Last result<select value={filters.last_outcome || ''} onChange={(e) => sf('last_outcome', e.target.value)}><option value="">any</option>{(segs?.last_outcome || []).map((o) => <option key={o.v} value={o.v}>{label(o.v)} ({o.n})</option>)}</select></label>
            <label>Search<input value={filters.search || ''} onChange={(e) => sf('search', e.target.value)} /></label>
            <label>Voice<select value={f.voice} onChange={(e) => setF({ ...f, voice: e.target.value })}><option value="">default</option>{['marin', 'cedar', 'coral', 'sage', 'ballad', 'verse', 'ash', 'alloy', 'echo', 'fable', 'nova', 'onyx', 'shimmer'].map((v) => <option key={v}>{v}</option>)}</select></label>
          </div>
        </details>
        <div className="aud"><b>{aud ? aud.count : '…'}</b> customers in this segment{aud?.sample?.length ? <span dir="auto"> · {aud.sample.map((s) => s.name).join(', ')}…</span> : null}</div>
        <div className="actions">
          <button className="btn" disabled={!f.name || !f.objective} onClick={() => create(false)}>Save</button>
          <button className="btn solid" disabled={!f.name || !f.objective || !aud?.count || (when.later && !when.at)} onClick={() => create(true)}>{when.later ? 'Schedule campaign' : 'Start calling'}</button>
        </div>
      </div>
    </Panel>
  )
}

export default function Campaigns() {
  const [list, reload] = usePoll('/campaigns', 3000)
  const [kbs] = usePoll('/kb', 0)
  const [sp, setSp] = useSearchParams()
  const loc = useLocation()
  const nav = useNavigate()
  const [creating, setCreating] = useState(sp.get('new') === '1')
  const [toast, setToast] = useState('')
  useEffect(() => { if (sp.get('new')) setSp({}, { replace: true }) }, [sp, setSp])
  const act = async (path, msg) => { try { await api.post(path); setToast(msg) } catch (e) { setToast(e.message) } reload() }

  return (
    <div className="page">
      <header className="page-h">
        <h1>Campaigns</h1>
        <div className="actions"><button className="btn solid" onClick={() => setCreating(true)}>+ New campaign</button></div>
      </header>
      {toast && <div className="notice" onClick={() => setToast('')}>{toast}</div>}
      {creating && <Form initial={{ filters: loc.state?.filters, form: {} }} onDone={() => { setCreating(false); reload() }} />}
      <div className="cards">
        {(list || []).map((c) => {
          const p = c.progress, pct = p.total ? Math.round(((p.completed + p.missed) / p.total) * 100) : 0
          return (
            <Panel key={c.id} className="camp">
              <div className="camp-h"><h4>{c.name}</h4><Badge kind={c.status === 'active' ? 'good' : ''}>{c.status}</Badge></div>
              {c.kb_id && <p className="muted small">Knowledge: {(kbs || []).find((k) => k.id === c.kb_id)?.name || '…'}</p>}
              <p className="obj">{c.objective}</p>
              <div className="meter"><i style={{ width: `${pct}%` }} /></div>
              <p className="muted small">{p.completed} talked · {p.missed} missed · {p.queued} pending of {c.audience}</p>
              <p className="muted small">
                {c.call_window ? `Calls ${c.call_window.start}–${c.call_window.end}` : 'Any time'}
                {c.next_call_at ? ` · next ${new Date(c.next_call_at * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}` : ''}
                {c.followup?.nudge_max ? ` · follow-up after ${c.followup.nudge_days}d` : ''}
              </p>
              <div className="actions">
                {c.status !== 'active' && c.status !== 'scheduled' && <button className="btn small solid" onClick={() => act(`/campaigns/${c.id}/start`, 'Campaign started.')}>Start</button>}
                {c.status === 'active' && <button className="btn small ghost" onClick={() => act(`/campaigns/${c.id}/stop`, 'Campaign stopped.')}>Stop</button>}
                <button className="btn small ghost" onClick={() => nav(`/calls?campaign=${c.id}`)}>Results</button>
                <button className="link" onClick={async () => { await api.del(`/campaigns/${c.id}`); reload() }}>Delete</button>
              </div>
            </Panel>
          )
        })}
      </div>
      {list && !list.length && !creating && <Empty title="No campaigns yet" hint="Pick a segment, give the agent an objective and press start.">
        <button className="btn solid" onClick={() => setCreating(true)}>Create the first one</button></Empty>}
    </div>
  )
}
