import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, usePoll } from '../api'
import Avatar from '../Avatar'
import Icon from '../Icon'
import { EmptyState, PageHeader, Skeleton, useTitle } from '../ui'
import { useToast } from '../Feedback'

const ago = (ts) => {
  if (!ts) return ''
  const s = Math.max(0, Date.now() / 1000 - ts)
  return s < 3600 ? `${Math.max(1, Math.floor(s / 60))}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`
}
const stateOf = (p) => (p.status === 'done' ? ['Finished', 'done'] : !p.total_tasks ? ['Planning', 'plan'] : p.doing_tasks > 0 ? ['In progress', 'live'] : p.open_tasks === 0 ? ['All done', 'done'] : ['Active', 'idle'])

export default function Projects() {
  useTitle('Projects')
  const toast = useToast()
  const nav = useNavigate()
  const [projects, reload] = usePoll('/projects', 8000)
  const [team] = usePoll('/agents', 0)
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ name: '', goal: '', members: [] })
  const [show, setShow] = useState('active')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const list = projects || []
  const counts = { active: list.filter((p) => p.status !== 'done').length, done: list.filter((p) => p.status === 'done').length }
  const shown = useMemo(() => list.filter((p) => (show === 'all' || (show === 'done') === (p.status === 'done')) && (!q.trim() || `${p.name} ${p.goal}`.toLowerCase().includes(q.trim().toLowerCase()))), [list, show, q])
  const live = list.filter((p) => p.status !== 'done')
  const create = async () => {
    setBusy(true)
    try { const p = await api.post('/projects', f); setOpen(false); setF({ name: '', goal: '', members: [] }); await reload(); nav(`/projects/${p.id}`) } catch (e) { toast.err(e.message) } finally { setBusy(false) }
  }
  const card = (p) => {
    const [label, tone] = stateOf(p)
    return (
      <Link key={p.id} to={`/projects/${p.id}`} className="pj-card">
        <div className="pj-top"><span className={`pj-chip ${tone}`}>{tone === 'live' && <i className="dot live" />}{label}</span>{p.last_update && <span className="faint small">Updated {ago(p.last_update)}</span>}</div>
        <h3>{p.name}</h3>
        <p className="goal">{p.goal || 'No goal written yet.'}</p>
        <div className="pj-prog">
          <div className="pj-meta"><b>{p.progress || 0}%</b><span>{p.total_tasks ? `${p.total_tasks - p.open_tasks} of ${p.total_tasks} tasks done` : 'Waiting for a plan'}</span></div>
          <div className="pj-bar"><i style={{ width: `${p.progress || 0}%` }} /></div>
        </div>
        <div className="pj-foot">
          <span className="faces">{p.members.slice(0, 5).map((m) => <Avatar key={m.id} agent={m} size={30} />)}{p.members.length > 5 && <i className="more">+{p.members.length - 5}</i>}{p.members.length === 0 && <span className="faint small">No one yet</span>}</span>
          <span className="pj-stats"><span title="Delivered files"><Icon name="drive" size={14} /> {p.deliverables}</span><span title="Open tasks"><Icon name="todo" size={14} /> {p.open_tasks}</span></span>
        </div>
      </Link>
    )
  }
  return (
    <div className="page wide pj-page">
      <PageHeader title="Projects" sub="Shared goals your team works on together: a plan, tasks, files and updates in one place." actions={<><Link className="btn big" to="/templates">From a template</Link><button className="btn solid big" onClick={() => setOpen(!open)}><Icon name="plus" size={15} /> New project</button></>} />

      {list.length > 0 && (
        <section className="pulse" aria-label="Projects at a glance">
          <div className="pcell"><span className="k">Active projects</span><b>{counts.active}</b><span className="s">{counts.done} finished</span></div>
          <div className="pcell"><span className="k">Open tasks</span><b>{live.reduce((n, p) => n + p.open_tasks, 0)}</b><span className="s">across active projects</span></div>
          <div className="pcell"><span className="k">Working now</span><b>{live.reduce((n, p) => n + (p.doing_tasks || 0), 0)}</b><span className="s">tasks in progress</span></div>
          <div className="pcell"><span className="k">Files delivered</span><b>{list.reduce((n, p) => n + p.deliverables, 0)}</b><span className="s">on project drives</span></div>
        </section>
      )}

      {open && (
        <section className="pj-new">
          <h3>New project</h3>
          <label className="f">Name<input autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Spring boiler promotion" /></label>
          <label className="f">Goal<textarea rows="2" value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} placeholder="What should be true when it is finished?" /></label>
          <div className="f">Who is on it
            <div className="pickrow">{(team || []).map((a) => (
              <label key={a.id} className={`pick ${f.members.includes(a.id) ? 'on' : ''}`}><input type="checkbox" checked={f.members.includes(a.id)} onChange={(e) => setF({ ...f, members: e.target.checked ? [...f.members, a.id] : f.members.filter((x) => x !== a.id) })} /><Avatar agent={a} size={26} /> {a.name}</label>
            ))}</div>
          </div>
          <p className="faint small">Once it is created, the team plans it: they break the goal into tasks and start working.</p>
          <div className="actions"><button className="btn solid" disabled={!f.name.trim() || busy} onClick={create}>{busy ? 'Creating…' : 'Create project'}</button><button className="btn" onClick={() => setOpen(false)}>Cancel</button></div>
        </section>
      )}

      {list.length > 0 && (
        <div className="pj-bar-row">
          <div className="seg" role="tablist">
            {[['active', 'Active', counts.active], ['done', 'Finished', counts.done], ['all', 'All', list.length]].map(([k, n, c]) => <button key={k} className={show === k ? 'on' : ''} role="tab" aria-selected={show === k} onClick={() => setShow(k)}>{n} <i>{c}</i></button>)}
          </div>
          <div className="search-box"><Icon name="search" size={15} /><input placeholder="Search projects" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search projects" /></div>
        </div>
      )}

      {!projects && <Skeleton rows={3} h={120} />}
      {projects && list.length === 0 && !open && <EmptyState title="No projects yet" hint="A project gives a few people a shared goal, a task list and a drive. Your manager will plan it for you." action={<button className="btn solid" onClick={() => setOpen(true)}>New project</button>} />}
      {list.length > 0 && (
        <div className="pj-grid">
          {shown.map(card)}
          {show !== 'done' && !q && <button className="pj-card add" onClick={() => setOpen(true)}><span><Icon name="plus" size={22} /></span>New project</button>}
          {shown.length === 0 && q && <p className="muted">Nothing matches “{q}”.</p>}
        </div>
      )}
    </div>
  )
}
