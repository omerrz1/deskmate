import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, usePoll } from '../api'
import Avatar from '../Avatar'
import PersonAvatar, { AiTag, RoleBadge } from '../PersonAvatar'
import { useAuth } from '../Auth'
import Icon from '../Icon'
import { Skeleton, useTitle } from '../ui'
import { useToast } from '../Feedback'

const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '')
const ext = (f) => (f.kind === 'sheet' ? 'XLS' : f.kind === 'chart' ? 'SVG' : f.kind === 'file' ? (f.title.split('.').pop() || 'FILE').slice(0, 4).toUpperCase() : 'DOC')
const STAGE = { planning: 'Planning', executing: 'Working', review: 'Finishing' }

function DocViewer({ id, onClose }) {
  const [d] = usePoll(`/docs/${id}`, 0, [id])
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-h"><h3>{d ? d.title : 'Opening…'}</h3><div className="actions"><a className="btn small" href={`/api/docs/${id}/download`}>Download</a><button className="btn small ghost" onClick={onClose}>Close</button></div></div>
        <div className="modal-b">
          {!d && <p className="muted">Loading…</p>}
          {d && d.kind === 'sheet' && d.table && <div className="sheet"><table><thead><tr>{d.table.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead><tbody>{d.table.rows.slice(0, 200).map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table></div>}
          {d && d.kind === 'chart' && <div dangerouslySetInnerHTML={{ __html: d.body }} />}
          {d && d.kind === 'file' && <p className="muted">This is an exported file. Use Download to open it.</p>}
          {d && d.kind === 'doc' && <pre className="docview" style={{ maxHeight: 'none' }}>{d.body}</pre>}
        </div>
      </div>
    </div>
  )
}

export default function Project() {
  const { id } = useParams()
  const toast = useToast()
  const [p, reload] = usePoll(`/projects/${id}`, 4000, [id])
  const [team] = usePoll('/agents', 0)
  const [task, setTask] = useState({ agent_id: '', title: '' })
  const [invite, setInvite] = useState('')
  const [person, setPerson] = useState('')
  const { isAdmin } = useAuth()
  const [directory] = usePoll('/people/directory', 0)
  const [text, setText] = useState('')
  const [view, setView] = useState(null)
  const [open, setOpen] = useState(null)
  const [more, setMore] = useState({})
  useTitle(p?.name)
  if (!p) return <div className="page wide"><Skeleton rows={5} /></div>
  const guard = async (fn) => { try { await fn(); reload() } catch (e) { toast.err(e.message) } }
  const outside = (team || []).filter((a) => !p.members.some((m) => m.id === a.id))
  const addable = (directory || []).filter((d) => d.role === 'member' && !(p.people || []).some((m) => m.id === d.id))  // admins already see every project
  const doing = p.tasks.filter((t) => t.status === 'doing')
  const todo = p.tasks.filter((t) => t.status === 'todo')
  const done = p.tasks.filter((t) => t.status === 'done')
  const doneShown = more.done ? done : done.slice(-4)
  const state = p.status === 'done' ? ['Finished', 'done'] : !p.tasks.length ? ['Planning', 'plan'] : doing.length ? ['In progress', 'live'] : todo.length ? ['Active', 'idle'] : ['All done', 'done']

  const taskRow = (t) => (
    <article key={t.id} className={`pd-task ${t.status}`}>
      <div className="pd-task-top" style={{ cursor: t.steps_total ? 'pointer' : 'default' }} onClick={() => t.steps_total && setOpen(open === t.id ? null : t.id)}>
        <Avatar agent={{ name: t.who, color: t.who_color }} size={34} />
        <div className="grow">
          <b>{t.title}</b>
          <span className="small muted">{t.who || 'Unassigned'}{t.due ? ` · due ${t.due}` : ''}{t.priority === 'high' ? ' · high priority' : ''}</span>
          {t.steps_total > 0 && t.status !== 'todo' && <div className="pd-steps"><div className="pj-bar thin"><i style={{ width: `${Math.round(100 * t.steps_done / t.steps_total)}%` }} /></div><span className="small muted">{t.steps_done} of {t.steps_total} steps</span></div>}
        </div>
        {t.status === 'done' ? <span className="tag done">Done</span> : t.status === 'doing' ? <span className={`tag ${t.stage === 'planning' ? 'plan' : 'doing'}`}>{STAGE[t.stage] || 'Working'}</span> : <span className="tag">To do</span>}
      </div>
      {open === t.id && (
        <div className="pd-task-more">
          <ol className="chk">{t.checklist.map((c, i) => <li key={i} className={c.done ? 'done' : ''}>{c.text}</li>)}</ol>
          {t.plan_doc_id && <button className="btn small" onClick={() => setView(t.plan_doc_id)}>Read the plan</button>}
          {t.result && <p className="small muted">{t.result}</p>}
        </div>
      )}
    </article>
  )
  const group = (title, rows, tone) => rows.length > 0 && (
    <div className="pd-group"><h4><span className={`dotc ${tone}`} />{title} <i>{rows.length}</i></h4>{rows.map(taskRow)}</div>
  )
  const fileList = (key, rows) => {
    const shown = more[key] ? rows : rows.slice(0, 5)
    return (
      <>
        {rows.length === 0 && <div className="pj-empty">Nothing yet.</div>}
        {shown.map((f) => (
          <button key={f.id} className="pd-file" onClick={() => setView(f.id)}>
            <span className="ico">{ext(f)}</span>
            <span className="grow"><b>{f.title}</b><small>{f.owner ? `${f.owner} · ` : ''}{when(f.updated_at)}</small></span>
          </button>
        ))}
        {rows.length > 5 && <button className="link" style={{ margin: '8px 0 0', padding: 0 }} onClick={() => setMore({ ...more, [key]: !more[key] })}>{more[key] ? 'Show fewer' : `Show all ${rows.length}`}</button>}
      </>
    )
  }
  return (
    <div className="page wide pd-page">
      <Link to="/projects" className="back"><Icon name="back" size={14} /> Projects</Link>
      <header className="pd-hero">
        <div className="pd-title">
          <span className={`pj-chip ${state[1]}`}>{state[1] === 'live' && <i className="dot live" />}{state[0]}</span>
          <h1>{p.name}</h1>
          {p.goal && <p className="goal">{p.goal}</p>}
        </div>
        <div className="actions">
          {isAdmin && p.tasks.length === 0 && <button className="btn" onClick={() => guard(async () => { const r = await api.post(`/projects/${id}/plan`); if (!r.planning_task) toast.info('Add someone to the project first.') })}>Ask the team to plan it</button>}
          <Link className="btn" to={`/files?drive=-${id}`}><Icon name="folder" size={14} /> Project files</Link>
          {isAdmin && <button className="btn" onClick={() => guard(() => api.patch(`/projects/${id}`, { status: p.status === 'done' ? 'active' : 'done' }))}>{p.status === 'done' ? 'Reopen' : 'Mark finished'}</button>}
        </div>
        <div className="pd-stats">
          <div className="pd-prog"><div className="pj-meta"><b>{p.progress}%</b><span>{done.length} of {p.tasks.length} tasks done</span></div><div className="pj-bar"><i style={{ width: `${p.progress}%` }} /></div></div>
          <div className="pcell"><span className="k">Working now</span><b>{doing.length}</b></div>
          <div className="pcell"><span className="k">To do</span><b>{todo.length}</b></div>
          <div className="pcell"><span className="k">Files delivered</span><b>{p.deliverables.length}</b></div>
          <div className="pcell"><span className="k">People</span><b>{p.members.length + (p.people || []).length}</b></div>
        </div>
      </header>

      <div className="pd-grid">
        <div className="pd-main">
          <section className="pd-card">
            <h3>Tasks</h3>
            {p.tasks.length === 0 && <div className="pj-empty big">Nothing planned yet. Ask the team to plan it, or add the first task below.</div>}
            {group('In progress', doing, 'live')}
            {group('To do', todo, 'idle')}
            {done.length > 0 && (
              <div className="pd-group"><h4><span className="dotc done" />Done <i>{done.length}</i></h4>{doneShown.map(taskRow)}
                {done.length > 4 && <button className="link" style={{ padding: 0, marginTop: 6 }} onClick={() => setMore({ ...more, done: !more.done })}>{more.done ? 'Show fewer' : `Show all ${done.length}`}</button>}</div>
            )}
            <div className="pd-add">
              <select value={task.agent_id} onChange={(e) => setTask({ ...task, agent_id: e.target.value })}><option value="">{isAdmin ? 'Give to…' : 'Ask…'}</option>{p.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select>
              <input placeholder={isAdmin ? 'Add a task' : 'What do you need from them?'} value={task.title} onChange={(e) => setTask({ ...task, title: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter' && task.agent_id && task.title.trim()) e.currentTarget.nextSibling.click() }} />
              <button className="btn solid" disabled={!task.agent_id || !task.title.trim()} onClick={() => guard(async () => { if (isAdmin) await api.post('/tasks', { agent_id: +task.agent_id, title: task.title, project_id: +id }); else await api.post(`/projects/${id}/tasks`, { agent_id: +task.agent_id, title: task.title }); setTask({ agent_id: '', title: '' }) })}>{isAdmin ? 'Add' : 'Ask'}</button>
            </div>
          </section>

          <section className="pd-card">
            <h3>Updates</h3>
            <form className="pd-post" onSubmit={(e) => { e.preventDefault(); if (text.trim()) guard(async () => { await api.post(`/projects/${id}/updates`, { text }); setText('') }) }}>
              <input placeholder="Post an update for the team" value={text} onChange={(e) => setText(e.target.value)} /><button className="btn">Post</button>
            </form>
            {p.updates.length === 0 && <div className="pj-empty">Nothing yet.</div>}
            {p.updates.slice(0, 10).map((u) => (
              <div key={u.id} className="pd-upd">{u.by_person ? <PersonAvatar name={u.who} size={32} /> : <Avatar agent={{ name: u.who, color: u.who_color || '#6b6b6b' }} size={32} />}<div><span><b>{u.mine ? 'You' : u.who}</b>{!u.by_person && <> <AiTag /></>} <span className="muted small">{when(u.ts)}</span></span><p>{u.text}</p></div></div>
            ))}
          </section>
        </div>

        <aside className="pd-side">
          <section className="pd-card">
            <h3>People <i className="cnt">{(p.people || []).length}</i></h3>
            {(p.people || []).length === 0 && <div className="pj-empty">Admins can open every project. No one else has been added.</div>}
            {(p.people || []).map((m) => (
              <div key={m.id} className="pd-person"><PersonAvatar name={m.name} size={36} /><span className="grow"><b>{m.name}</b><small>{m.title || 'Works on this project'}</small></span><RoleBadge role={m.role} />{isAdmin && <button className="link" onClick={() => guard(() => api.del(`/projects/${id}/people/${m.id}`))}>Remove</button>}</div>
            ))}
            {isAdmin && addable.length > 0 && (
              <div className="pd-add inline">
                <select value={person} onChange={(e) => setPerson(e.target.value)}><option value="">Add a person…</option>{addable.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
                <button className="btn" disabled={!person} onClick={() => guard(async () => { await api.post(`/projects/${id}/people`, { person_id: +person }); setPerson('') })}>Add</button>
              </div>
            )}
          </section>
          <section className="pd-card">
            <h3>AI team <AiTag /></h3>
            {p.members.map((m) => (
              <div key={m.id} className="pd-person"><Avatar agent={m} size={36} />{isAdmin ? <Link className="grow" to={`/agents/${m.id}`}><b>{m.name}</b><small>{m.job}</small></Link> : <span className="grow"><b>{m.name}</b><small>{m.job}</small></span>}{isAdmin && <button className="link" onClick={() => guard(() => api.del(`/projects/${id}/members/${m.id}`))}>Remove</button>}</div>
            ))}
            {isAdmin && outside.length > 0 && (
              <div className="pd-add inline">
                <select value={invite} onChange={(e) => setInvite(e.target.value)}><option value="">Add an AI employee…</option>{outside.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
                <button className="btn" disabled={!invite} onClick={() => guard(async () => { await api.post(`/projects/${id}/members`, { agent_id: +invite, task: '' }); setInvite('') })}>Add</button>
              </div>
            )}
          </section>
          <section className="pd-card"><h3>Delivered files</h3>{fileList('deliverables', p.deliverables)}</section>
          {isAdmin && <section className="pd-card"><h3>Work in progress</h3>{fileList('working', p.working_files)}</section>}
        </aside>
      </div>
      {view && <DocViewer id={view} onClose={() => setView(null)} />}
    </div>
  )
}
