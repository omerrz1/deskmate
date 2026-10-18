import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, usePoll } from '../api'
import Avatar from '../Avatar'
import { CompanyForm } from './SettingsHub'
import { AppIcon, PageHeader, useTitle } from '../ui'
import { useToast } from '../Feedback'

const when = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Seven tiny bars: tasks finished per day this week. */
function Spark({ week }) {
  const max = Math.max(1, ...week.map((d) => d.n))
  return (
    <svg className="spark" viewBox="0 0 70 24" width="70" height="24" role="img" aria-label={`Tasks finished in the last 7 days: ${week.map((d) => `${d.day} ${d.n}`).join(', ')}`}>
      {week.map((d, i) => { const h = Math.max(2, (d.n / max) * 22); return <rect key={i} x={i * 10} y={24 - h} width="7" height={h} rx="2" className={i === week.length - 1 ? 'now' : ''} /> })}
    </svg>
  )
}

const STARTERS = [
  ['A caller', 'female', 'Phones customers for the company: follows up on enquiries, answers questions from the knowledge base, and agrees the next step. Friendly, quick, short sentences.'],
  ['A manager', 'male', 'The team manager. Runs the other employees: hands out tasks, checks on progress, fixes problems, runs the platform like the admin and reports back to the admin every day. Calm, organised and decisive.'],
  ['An office assistant', 'female', 'Office assistant: handles email, keeps spreadsheets and documents tidy, writes reports, tracks to-dos and reminders. Thorough, organised and concise.'],
]

export default function Home() {
  useTitle('Team')
  const toast = useToast()
  const nav = useNavigate()
  const [agents, reload] = usePoll('/agents', 5000)
  const [att, reloadAtt] = usePoll('/attention', 5000)
  const [company, reloadC] = usePoll('/company', 0)
  const [talk, reloadTalk] = usePoll('/discuss', 8000)
  const [central, reloadCentral] = usePoll('/central', 10000)
  const [pulse] = usePoll('/pulse', 8000)
  const [params, setParams] = useSearchParams()
  const [panel, setPanel] = useState(null) // 'new' | 'meet' | 'manager'
  const [sel, setSel] = useState([])
  const [form, setForm] = useState({ name: '', gender: 'female', brief: '' })
  const [busy, setBusy] = useState(false)
  const [when_, setWhen] = useState({})
  const [answers, setAnswers] = useState({})

  const list = agents || []
  useEffect(() => {  // the command palette links here: ?new=1 opens the new-agent form, ?meet=1 the team-meeting picker
    if (!agents) return
    if (params.get('new')) setPanel('new')
    else if (params.get('meet') && agents.length) { setPanel('meet'); setSel(agents.map((a) => a.id)) }
    if (params.get('new') || params.get('meet')) setParams({}, { replace: true })
  }, [agents]) // eslint-disable-line react-hooks/exhaustive-deps
  const guard = async (fn) => { try { await fn() } catch (e) { toast.err(e.message) } }
  const meet = (ids) => guard(async () => { const m = await api.post('/meetings', { agent_ids: ids }); nav(`/meeting/${m.id}`) })
  const createAgent = () => guard(async () => {
    setBusy(true)
    try { const a = await api.post('/agents', form); setPanel(null); setForm({ name: '', gender: 'female', brief: '' }); await reload(); nav(`/agents/${a.id}`) } finally { setBusy(false) }
  })
  const makeManager = (id) => guard(async () => { await api.put(`/agents/${id}/manager`, { is_manager: true }); setPanel(null); reload(); reloadAtt() })
  const newManager = () => guard(async () => {
    setBusy(true)
    try {
      const a = await api.post('/agents', { gender: 'male', brief: STARTERS[1][2] })
      await api.put(`/agents/${a.id}/manager`, { is_manager: true }); await reload(); reloadAtt(); nav(`/agents/${a.id}`)
    } finally { setBusy(false) }
  })
  const startReq = (r) => guard(async () => { const x = await api.post(`/requests/${r.id}/start`); nav(`/meeting/${x.meeting_id}`) })
  const schedReq = (r) => guard(async () => { await api.post(`/requests/${r.id}/schedule`, { when: when_[r.id] }); reloadAtt() })

  const needs = !!att && !!(att.meetings.length || att.approvals || att.unread || att.needs_manager || att.connectors_to_review || (att.app_requests || []).length || (talk || []).length)
  const onboarding = company && (!company.name || list.length === 0)
  const waiting = att ? att.unread + att.approvals + att.meetings.length + (att.app_requests || []).length + (talk || []).length : 0

  return (
    <div className="page wide home">
      <PageHeader title="Team" sub="Everyone on your team and what they are working on."
        actions={<><Link className="btn" to="/team-chat">Team chat</Link><Link className="btn" to="/templates">Templates</Link>{list.length > 0 && <Link className="btn" to="/performance">Performance</Link>}{list.length > 1 && <button className="btn" onClick={() => { setPanel(panel === 'meet' ? null : 'meet'); setSel(list.map((a) => a.id)) }}>Team meeting</button>}<button className="btn solid" onClick={() => setPanel(panel === 'new' ? null : 'new')}>New agent</button></>} />
      {central?.work_paused && <div className="notice" onClick={async () => { await api.put('/central', { work_paused: false }); reloadCentral() }}>Work is paused. Click here to resume.</div>}

      {!onboarding && list.length > 0 && pulse && (
        <section className="pulse" aria-label="Team overview">
          <div className="pcell"><span className="k">Working now</span><b>{pulse.working}<small> / {pulse.total}</small></b><span className="s">{pulse.total - pulse.working} idle</span></div>
          <div className="pcell"><span className="k">Open to-dos</span><b>{pulse.open_todos}</b><span className="s">across the team</span></div>
          <Link to="/inbox" className={`pcell link ${waiting > 0 ? 'hot' : ''}`}><span className="k">Needs you</span><b>{waiting}</b><span className="s">{waiting > 0 ? 'open inbox' : 'all clear'}</span></Link>
          <div className="pcell"><span className="k">Done today</span><b>{pulse.done_today}</b><span className="s"><Spark week={pulse.week} /></span></div>
        </section>
      )}

      {onboarding && (
        <section className="onboard">
          <h2>Set up your company</h2>
          <ol className="stepper onboard-steps"><li className={company.name ? 'past' : 'on'}><i>{company.name ? '✓' : 1}</i> Company</li><li className={company.name && list.length === 0 ? 'on' : list.length ? 'past' : ''}><i>{list.length ? '✓' : 2}</i> Team</li></ol>
          {!company.name && <CompanyForm onDone={reloadC} />}
          {company.name && list.length === 0 && (
            <>
              {company.pending_team && (
                <div className="tip" style={{ marginBottom: 10 }}>
                  <b>Your {company.pending_team.name} is ready to set up.</b> {company.pending_team.roles.join(', ')}. Each one writes their own name and personality, which costs a few cents.
                  <div className="actions" style={{ marginTop: 10 }}><button className="btn solid" disabled={busy} onClick={() => guard(async () => { setBusy(true); try { await api.post(`/templates/teams/${company.pending_team.id}/apply`); reloadC(); await reload() } finally { setBusy(false) } })}>{busy ? 'Setting them up…' : `Create the ${company.pending_team.name}`}</button></div>
                </div>
              )}
              <p className="muted">Who do you want on your team first? Each one writes their own name and personality. You can change everything later.</p>
              <p className="small"><Link to="/templates" className="link">Or start from a ready-made team: sales, support, back office, marketing, recruiting →</Link></p>
              <div className="pickrow">{STARTERS.map(([n, g, brief]) => <button key={n} className="pick" disabled={busy} onClick={() => guard(async () => { setBusy(true); try { const a = await api.post('/agents', { gender: g, brief }); if (n === 'A manager') await api.put(`/agents/${a.id}/manager`, { is_manager: true }); await reload() } finally { setBusy(false) } })}>{n}</button>)}</div>
              {busy && <p className="muted small">Writing their profile…</p>}
            </>
          )}
        </section>
      )}

      {needs && (
        <section className="needs">
          <h2>Needs you</h2>
          {att.meetings.map((r) => (
            <div key={r.id} className={`need ${r.urgency === 'urgent' ? 'urgent' : ''}`}>
              <Avatar agent={{ name: r.agent_name, color: r.agent_color }} size={28} />
              <div className="grow">
                <b>{r.agent_name} wants a meeting: {r.topic}</b>
                <p className="small muted">{r.reason}{r.status === 'scheduled' ? ` · booked for ${when(r.scheduled_at)}` : ''}</p>
              </div>
              <div className="need-act">
                <button className="btn small solid" onClick={() => startReq(r)}>Join now</button>
                {when_[r.id] === undefined ? <button className="link" onClick={() => setWhen({ ...when_, [r.id]: '' })}>Schedule</button> : (
                  <>
                    <input type="datetime-local" value={when_[r.id]} onChange={(e) => setWhen({ ...when_, [r.id]: e.target.value })} />
                    <button className="btn small" disabled={!when_[r.id]} onClick={() => schedReq(r)}>Book</button>
                  </>
                )}
                <button className="link" onClick={() => api.post(`/requests/${r.id}/decline`).then(reloadAtt)}>Not now</button>
              </div>
            </div>
          ))}
          {(att.app_requests || []).map((r) => (
            <div key={r.id} className="need">
              <AppIcon id={r.icon} name={r.app_name} size={28} />
              <div className="grow">
                <b>{r.agent_name} needs {r.app_name}</b>
                <p className="small muted">{r.why}</p>
                <details><summary className="small muted">How to set it up</summary><ol className="steps small">{r.steps.map((s, i) => <li key={i}>{s}</li>)}</ol></details>
              </div>
              <div className="need-act">
                <Link className="btn small solid" to={r.icon ? `/apps?connect=${r.icon}` : '/apps'}>Set it up</Link>
                <button className="link" onClick={() => api.post(`/app-requests/${r.id}/dismiss`).then(reloadAtt)}>Not now</button>
              </div>
            </div>
          ))}
          {(talk || []).slice(0, 3).map((d) => (
            <div key={d.id} className="need">
              <div className="grow">
                <b>{d.agent_name || 'Your team'} asks: {d.topic}</b>
                {d.detail && <p className="small muted">{d.detail}</p>}
                <div className="actions" style={{ marginTop: 6 }}>
                  <input style={{ flex: 1, maxWidth: 360 }} placeholder="Your answer" value={answers[d.id] || ''} onChange={(e) => setAnswers({ ...answers, [d.id]: e.target.value })} />
                  <button className="btn small solid" disabled={!(answers[d.id] || '').trim()} onClick={() => guard(async () => { await api.post(`/discuss/${d.id}/answer`, { answer: answers[d.id], remember: true }); setAnswers({ ...answers, [d.id]: '' }); reloadTalk(); reloadAtt() })}>Answer</button>
                  <button className="link" onClick={() => api.post(`/discuss/${d.id}/dismiss`).then(() => { reloadTalk(); reloadAtt() })}>Skip</button>
                </div>
              </div>
            </div>
          ))}
          {att.approvals > 0 && <div className="need"><div className="grow"><b>{att.approvals} email{att.approvals === 1 ? '' : 's'} waiting for your OK</b></div><Link className="btn small solid" to="/inbox">Review</Link></div>}
          {att.unread > 0 && <div className="need"><div className="grow"><b>{att.unread} new message{att.unread === 1 ? '' : 's'}</b></div><Link className="btn small" to="/inbox">Open</Link></div>}
          {att.connectors_to_review > 0 && <div className="need"><div className="grow"><b>A manager connected a new tool</b></div><Link className="btn small" to="/apps">Review</Link></div>}
          {att.needs_manager && <div className="need"><div className="grow"><b>Set up a manager</b><p className="small muted">A manager hands out tasks, checks progress and reports back to you.</p></div><button className="btn small solid" onClick={() => setPanel('manager')}>Set up</button></div>}
        </section>
      )}

      {panel === 'manager' && (
        <section className="panel"><div className="panel-b">
          <h3>Who should be the manager?</h3>
          <div className="pickrow">{list.map((a) => <button key={a.id} className="pick" onClick={() => makeManager(a.id)}><Avatar agent={a} size={26} /> {a.name}</button>)}</div>
          <button className="btn solid" disabled={busy} onClick={newManager}>{busy ? 'Creating…' : 'Create a new manager'}</button>
        </div></section>
      )}

      {panel === 'meet' && (
        <section className="panel"><div className="panel-b">
          <h3>Who joins the meeting?</h3>
          <div className="pickrow">{list.map((a) => (
            <label key={a.id} className={`pick ${sel.includes(a.id) ? 'on' : ''}`}><input type="checkbox" checked={sel.includes(a.id)} onChange={(e) => setSel(e.target.checked ? [...sel, a.id] : sel.filter((x) => x !== a.id))} /><Avatar agent={a} size={26} /> {a.name}</label>
          ))}</div>
          <button className="btn solid" disabled={!sel.length} onClick={() => meet(sel)}>Start meeting</button>
        </div></section>
      )}

      {panel === 'new' && (
        <section className="panel"><div className="panel-b form-grid">
          <h3>New agent</h3>
          <label className="f">What will they do?<textarea rows="3" value={form.brief} onChange={(e) => setForm({ ...form, brief: e.target.value })} placeholder="e.g. Chases people who went quiet after applying. Friendly but persistent." /></label>
          <div className="row2">
            <label className="f">Gender<select value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}><option value="female">Female</option><option value="male">Male</option><option value="non-binary">Non-binary</option></select></label>
            <label className="f">Name (optional)<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          </div>
          <div className="actions"><button className="btn solid" disabled={busy} onClick={createAgent}>{busy ? 'Creating…' : 'Create'}</button><button className="btn" onClick={() => setPanel(null)}>Cancel</button></div>
        </div></section>
      )}

      {list.length > 0 && (
        <div className="agents">
          {list.map((a, idx) => (
            <Link to={`/agents/${a.id}`} className={`agent-sq ${a.working ? 'working' : ''}`} key={a.id} style={{ '--i': idx }}>
              <div className="top">
                <Avatar agent={a} size={52} />
                {!!a.is_manager && <span className="tag mgr" style={{ margin: 0 }}>Manager</span>}
              </div>
              <div>
                <h3>{a.name}</h3>
                <p className="role">{a.role || 'Team member'}</p>
              </div>
              <div className="st">{a.working ? <><i className="dot live" /><span>{a.current_task}</span></> : <span className="faint">Idle</span>}</div>
              <div className="foot"><span>{a.open_duties} to-do{a.open_duties === 1 ? '' : 's'}</span></div>
            </Link>
          ))}
          <button className="agent-sq add" onClick={() => setPanel('new')}><span style={{ fontSize: 22, lineHeight: 1 }}>+</span>New agent</button>
        </div>
      )}
    </div>
  )
}
