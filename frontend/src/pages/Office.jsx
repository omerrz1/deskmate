import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, apiFetch, usePoll } from '../api'
import Avatar from '../Avatar'
import FileManager from '../FileManager'
import { Mail } from '../Mail'

const TABS = [['tasks', 'To-do'], ['mail', 'Mail'], ['files', 'Files'], ['calendar', 'Calendar'], ['playbooks', 'Playbooks']]
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '')

export default function Office() {
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'tasks'
  const [sum] = usePoll('/office/summary', 5000)
  const [team] = usePoll('/agents', 0)
  return (
    <div className="page">
      <header className="page-h"><div><h1>Office</h1><p className="muted">Where your team keeps its work: to-dos, mail, documents, calendar and know-how.</p></div></header>
      <div className="tabs">
        {TABS.map(([k, n]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setSp({ tab: k })}>
            {n}{k === 'mail' && sum && sum.unread + sum.approvals > 0 && <i className="badge">{sum.unread + sum.approvals}</i>}{k === 'tasks' && sum && sum.doing > 0 && <i className="dot live" />}
          </button>
        ))}
      </div>
      {tab === 'tasks' && <Tasks team={team || []} initialAgent={sp.get('agent')} />}
      {tab === 'mail' && <Mail team={team || []} />}
      {tab === 'files' && <Files team={team || []} />}
      {tab === 'calendar' && <Calendar team={team || []} />}
      {tab === 'playbooks' && <Playbooks team={team || []} />}
    </div>
  )
}

/* ---------------- to-do ---------------- */
export function Tasks({ team, initialAgent, fixed }) {
  const [agent, setAgent] = useState(initialAgent || '')
  const [rows, reload] = usePoll(`/tasks${agent ? `?agent_id=${agent}` : ''}`, 4000, [agent])
  const [f, setF] = useState({ agent_id: initialAgent || '', title: '', detail: '', due: '', priority: 'normal', checklist: '', recur: '', run_now: false })
  const [adding, setAdding] = useState(false)
  const [msg, setMsg] = useState('')
  const add = async () => {
    if (!f.agent_id || !f.title.trim()) { setMsg('Choose who it is for and give it a title.'); return }
    try {
      await api.post('/tasks', { ...f, agent_id: +f.agent_id, checklist: f.checklist.split('\n').map((x) => x.trim()).filter(Boolean) })
      setF({ ...f, title: '', detail: '', due: '', checklist: '', recur: '', run_now: false }); setAdding(false); setMsg(''); reload()
    } catch (e) { setMsg(e.message) }
  }
  const cols = [['todo', 'To do'], ['plan', 'Planning'], ['doing', 'Executing'], ['done', 'Done']]
  const colOf = (t) => (t.status === 'doing' ? (t.stage === 'planning' ? 'plan' : 'doing') : t.status)
  return (
    <>
      <div className="actions" style={{ margin: '4px 0 14px' }}>
        {!fixed && <select value={agent} onChange={(e) => setAgent(e.target.value)}><option value="">Everyone</option>{team.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>}
        <button className="btn solid" onClick={() => setAdding(!adding)}>Give a task</button>
      </div>
      {msg && <div className="notice" onClick={() => setMsg('')}>{msg}</div>}
      {adding && (
        <section className="panel"><div className="panel-b form-grid">
          <div className="row2">
            <label className="f">For<select value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })}><option value="">Choose…</option>{team.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.role}</option>)}</select></label>
            <label className="f">Priority<select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option></select></label>
          </div>
          <label className="f">Task<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="e.g. Email the five warmest leads the sign-up link" /></label>
          <label className="f">Details<textarea rows="2" value={f.detail} onChange={(e) => setF({ ...f, detail: e.target.value })} /></label>
          <label className="f">Checklist (one step per line, optional)<textarea rows="3" value={f.checklist} onChange={(e) => setF({ ...f, checklist: e.target.value })} /></label>
          <div className="row2">
            <label className="f">Due (e.g. 2026-10-15T09:00)<input value={f.due} onChange={(e) => setF({ ...f, due: e.target.value })} /></label>
            <label className="f">Repeat<select value={f.recur} onChange={(e) => setF({ ...f, recur: e.target.value })}><option value="">Once</option><option value="daily">Every day</option><option value="weekly">Every week</option></select></label>
          </div>
          <label className="check"><input type="checkbox" checked={f.run_now} onChange={(e) => setF({ ...f, run_now: e.target.checked })} /> Let them start straight away and do it themselves</label>
          <div className="actions"><button className="btn solid" onClick={add}>Add task</button></div>
        </div></section>
      )}
      <div className="board">
        {cols.map(([k, name]) => {
          const list = (rows || []).filter((r) => colOf(r) === k)
          return (
            <section key={k} className="col"><h3>{name} <span className="muted small">{list.length}</span></h3>
              {list.length === 0 && <p className="muted small">Nothing here.</p>}
              {list.map((t) => (
                <article key={t.id} className={`task p-${t.priority}`}>
                  <div className="task-top"><Avatar agent={{ name: t.agent_name, color: t.agent_color }} size={22} /><b>{t.title}</b></div>
                  <p className="muted small">{t.agent_name}{t.priority === 'high' ? ' · high priority' : ''}{t.due ? ` · due ${t.due}` : ''}{t.recur ? ` · repeats ${t.recur}` : ''}{t.source === 'email' ? ' · from an email' : ''}</p>
                  {t.detail && <p className="small">{t.detail}</p>}
                  {t.checklist.length > 0 && t.status !== 'todo' && <span className="stagebar">{t.checklist.map((c, i) => <i key={i} className={c.done ? 'on' : (t.status === 'doing' && i === t.checklist.findIndex((x) => !x.done) ? 'cur' : '')} />)}</span>}
                  {t.checklist.length > 0 && <ul className="chk">{t.checklist.map((c, i) => <li key={i}><label><input type="checkbox" checked={c.done} onChange={async () => { await api.patch(`/tasks/${t.id}`, { toggle_step: i }); reload() }} /> <span className={c.done ? 'done' : ''}>{c.text}</span></label></li>)}</ul>}
                  {t.result && <p className="small result">{t.result}</p>}
                  <div className="duty-act">
                    {t.status === 'todo' && <button className="link" onClick={async () => { await api.post(`/duties/${t.id}/run`); reload() }}>Start</button>}
                    {t.status !== 'done' && <button className="link" onClick={async () => { await api.patch(`/tasks/${t.id}`, { status: 'done' }); reload() }}>Done</button>}
                    {t.status === 'done' && <button className="link" onClick={async () => { await api.patch(`/tasks/${t.id}`, { status: 'todo' }); reload() }}>Reopen</button>}
                    <button className="link" onClick={async () => { await api.del(`/duties/${t.id}`); reload() }}>Remove</button>
                  </div>
                </article>
              ))}
            </section>
          )
        })}
      </div>
    </>
  )
}

/* ---------------- mail ---------------- */
export function MailSettings() {
  const [s, reload] = usePoll('/office/settings', 0)
  const [f, setF] = useState({})
  const [msg, setMsg] = useState('')
  const [test, setTest] = useState('')
  useEffect(() => { if (s) setF({ ...s, smtp_pass: '' }) }, [s])
  if (!s) return null
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const save = async () => { try { await api.put('/office/settings', f); setMsg('Saved.'); reload() } catch (e) { setMsg(e.message) } }
  return (
    <section className="panel"><div className="panel-b form-grid">
      <h3>Mail settings</h3>
      <div className="row2"><label className="f">Company domain (for new addresses)<input value={f.company_domain || ''} onChange={set('company_domain')} placeholder="office.local" /></label>
        <label className="f">Your email address<input value={f.admin_email || ''} onChange={set('admin_email')} placeholder="you@yourcompany.com" /></label></div>
      <label className="f">Emails to outside addresses<select value={f.email_policy || 'approve'} onChange={set('email_policy')}><option value="approve">Wait for my approval (recommended)</option><option value="auto">Send straight away</option></select></label>
      <div className="row2"><label className="f">SMTP server<input value={f.smtp_host || ''} onChange={set('smtp_host')} placeholder="smtp.example.com" /></label><label className="f">Port<input value={f.smtp_port || ''} onChange={set('smtp_port')} placeholder="587" /></label></div>
      <div className="row2"><label className="f">SMTP username<input value={f.smtp_user || ''} onChange={set('smtp_user')} /></label><label className="f">SMTP password {s.smtp_pass_set && <span className="muted">(saved)</span>}<input type="password" value={f.smtp_pass || ''} onChange={set('smtp_pass')} placeholder={s.smtp_pass_set ? 'leave blank to keep' : ''} /></label></div>
      <div className="row2"><label className="f">IMAP server (incoming)<input value={f.imap_host || ''} onChange={set('imap_host')} placeholder="imap.example.com" /></label><label className="f">IMAP port<input value={f.imap_port || ''} onChange={set('imap_port')} placeholder="993" /></label></div>
      <div className="actions"><button className="btn solid" onClick={save}>Save settings</button>
        <input placeholder="Send a test to…" value={test} onChange={(e) => setTest(e.target.value)} style={{ width: 220 }} />
        <button className="btn" onClick={async () => { try { await api.post('/office/test-email', { to: test }); setMsg('Test email sent.') } catch (e) { setMsg(e.message) } }}>Send test</button></div>
      {msg && <p className="ok small">{msg}</p>}
    </div></section>
  )
}

/* ---------------- files ---------------- */
function Files({ team }) {
  const [owner, setOwner] = useState('shared')
  return (
    <>
      <div className="actions" style={{ margin: '4px 0 12px' }}>
        <select value={owner} onChange={(e) => setOwner(e.target.value)}><option value="shared">Shared drive</option>{team.map((a) => <option key={a.id} value={a.id}>{a.name}'s computer</option>)}</select>
        <span className="muted small">Each employee has their own folders. Anything you add here, they can open.</span>
      </div>
      <section className="panel"><div className="panel-b"><FileManager key={owner} owner={owner} /></div></section>
    </>
  )
}

/* ---------------- calendar ---------------- */
export function Calendar({ team }) {
  const [ev, reload] = usePoll('/events?days=60', 8000)
  const [f, setF] = useState({ title: '', start: '', end: '', attendees: [], notes: '' })
  const [msg, setMsg] = useState('')
  const days = {}
  ;(ev || []).forEach((e) => { const k = new Date(e.start * 1000).toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' }); (days[k] = days[k] || []).push(e) })
  const add = async () => {
    if (!f.title || !f.start) { setMsg('Give it a title and a start time.'); return }
    try { await api.post('/events', f); setF({ title: '', start: '', end: '', attendees: [], notes: '' }); setMsg(''); reload() } catch (e) { setMsg(e.message) }
  }
  return (
    <div className="two-col">
      <section className="panel"><div className="panel-b">
        <h3>Coming up</h3>
        {Object.keys(days).length === 0 && <p className="muted small">Nothing scheduled.</p>}
        {Object.entries(days).map(([day, list]) => (
          <div key={day} className="day"><h4>{day}</h4>
            {list.map((e) => (
              <div key={e.id} className="ev"><b>{new Date(e.start * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</b> {e.title}
                <span className="muted small"> {e.owner ? `· ${e.owner}` : ''}{e.attendees.length ? ` · with ${e.attendees.join(', ')}` : ''}</span>
                <button className="link" onClick={async () => { await api.del(`/events/${e.id}`); reload() }}>Cancel</button></div>
            ))}
          </div>
        ))}
      </div></section>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Add to the calendar</h3>
        {msg && <p className="warn small">{msg}</p>}
        <label className="f">What<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
        <div className="row2"><label className="f">Starts<input type="datetime-local" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} /></label><label className="f">Ends (optional)<input type="datetime-local" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} /></label></div>
        <div className="f">Who is in it<div className="pickrow">{team.map((a) => <label key={a.id} className={`pick ${f.attendees.includes(a.name) ? 'on' : ''}`}><input type="checkbox" checked={f.attendees.includes(a.name)} onChange={(e) => setF({ ...f, attendees: e.target.checked ? [...f.attendees, a.name] : f.attendees.filter((x) => x !== a.name) })} /><Avatar agent={a} size={26} /> {a.name}</label>)}</div></div>
        <label className="f">Notes<textarea rows="2" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
        <div className="actions"><button className="btn solid" onClick={add}>Add</button></div>
      </div></section>
    </div>
  )
}

/* ---------------- playbooks ---------------- */
function Playbooks({ team }) {
  const [rows, reload] = usePoll('/skills', 6000)
  const [f, setF] = useState({ name: '', description: '', steps: '', agent_id: '' })
  const [msg, setMsg] = useState('')
  const add = async () => {
    if (!f.name.trim() || !f.steps.trim()) { setMsg('Give it a name and the steps.'); return }
    await api.post('/skills', { ...f, agent_id: f.agent_id ? +f.agent_id : null }); setF({ name: '', description: '', steps: '', agent_id: f.agent_id }); setMsg(''); reload()
  }
  return (
    <div className="two-col">
      <section className="panel"><div className="panel-b">
        <h3>What your team knows how to do</h3>
        {(rows || []).length === 0 && <p className="muted small">Nothing taught yet. Teach a task on the right, or tell an agent in a meeting and they will write it down themselves.</p>}
        {(rows || []).map((s) => (
          <article key={s.id} className="skill"><div className="actions" style={{ justifyContent: 'space-between' }}><b>{s.name}</b><span className="muted small">{s.owner ? `${s.owner} only` : 'whole team'}{s.created_by ? ` · taught by ${s.created_by}` : ''}</span></div>
            {s.description && <p className="muted small">{s.description}</p>}<pre className="mailbody">{s.steps}</pre>
            <button className="link" onClick={async () => { await api.del(`/skills/${s.id}`); reload() }}>Remove</button></article>
        ))}
      </div></section>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Teach a task</h3>
        <p className="muted small">Write it like you would explain it to a new hire. They look it up before doing that kind of work.</p>
        {msg && <p className="warn small">{msg}</p>}
        <label className="f">Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Chase a warm lead" /></label>
        <label className="f">When to use it<input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        <label className="f">Steps<textarea rows="8" value={f.steps} onChange={(e) => setF({ ...f, steps: e.target.value })} placeholder={'1. Check the last call summary\n2. Call them\n3. If interested, send the sign-up link\n4. Log it in the call tracker'} /></label>
        <label className="f">Who learns it<select value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })}><option value="">The whole team</option>{team.map((a) => <option key={a.id} value={a.id}>{a.name} only</option>)}</select></label>
        <div className="actions"><button className="btn solid" onClick={add}>Teach</button></div>
      </div></section>
    </div>
  )
}

export { Mail }
