import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, usePoll } from '../api'
import Avatar from '../Avatar'
import { PageHeader, Skeleton, useTitle } from '../ui'
import { useConfirm, useToast } from '../Feedback'

/** Starting points: a whole team, a project with its tasks, or a ready-made playbook. */
export default function Templates() {
  useTitle('Templates')
  const toast = useToast()
  const confirm = useConfirm()
  const nav = useNavigate()
  const [t] = usePoll('/templates', 0)
  const [team] = usePoll('/agents', 0)
  const [busy, setBusy] = useState('')
  const [use, setUse] = useState(null) // the project template being set up
  const [who, setWho] = useState([])
  const [added, setAdded] = useState({})
  if (!t) return <div className="page wide"><Skeleton rows={4} h={120} /></div>
  const people = team || []

  const addTeam = async (tpl) => {
    if (!(await confirm({ title: `Add the ${tpl.name}?`, body: `${tpl.members.length} new employees are created: ${tpl.members.map((m) => m.role).join(', ')}. Each writes their own name and personality, which costs a few cents.`, confirm: 'Add the team' }))) return
    setBusy(tpl.id)
    try { await api.post(`/templates/teams/${tpl.id}/apply`); toast.ok(`${tpl.name} added`); nav('/') } catch (e) { toast.err(e.message) } finally { setBusy('') }
  }
  const startProject = async () => {
    setBusy(use.id)
    try { const r = await api.post(`/templates/projects/${use.id}/apply`, { members: who, name: use.name, goal: use.goal }); nav(`/projects/${r.id}`) } catch (e) { toast.err(e.message) } finally { setBusy('') }
  }
  const addPlaybook = async (b, agentId) => {
    try { const r = await api.post(`/templates/playbooks/${b.id}/apply`, { agent_id: agentId || null }); setAdded({ ...added, [b.id]: true }); toast.ok(r.added ? 'Playbook added' : 'They already have it') } catch (e) { toast.err(e.message) }
  }
  return (
    <div className="page wide">
      <PageHeader title="Templates" sub="Start from something that works: a whole team, a project with its tasks, or a playbook your team follows." />

      <h2 className="tp-h">Teams</h2>
      <div className="tp-grid">
        {t.teams.map((tpl) => (
          <article key={tpl.id} className="tp-card">
            <h3>{tpl.name}</h3><p className="muted">{tpl.blurb}</p>
            <div className="tp-roles">{tpl.members.map((m, i) => <span key={i} className={m.manager ? 'on' : ''}>{m.role}{m.manager ? ' · manager' : ''}</span>)}</div>
            <button className="btn solid" disabled={!!busy} onClick={() => addTeam(tpl)}>{busy === tpl.id ? 'Writing their profiles…' : 'Add this team'}</button>
          </article>
        ))}
      </div>

      <h2 className="tp-h">Projects</h2>
      <div className="tp-grid">
        {t.projects.map((p) => (
          <article key={p.id} className="tp-card">
            <h3>{p.name}</h3><p className="muted">{p.goal}</p>
            <ol className="tp-tasks">{p.tasks.map((x) => <li key={x}>{x}</li>)}</ol>
            <button className="btn" onClick={() => { setUse(p); setWho(people.map((a) => a.id)) }}>Use this project</button>
          </article>
        ))}
      </div>

      <h2 className="tp-h">Playbooks</h2>
      <div className="tp-grid">
        {t.playbooks.map((b) => (
          <article key={b.id} className="tp-card">
            <h3>{b.name}</h3><p className="muted">{b.description}</p>
            <details><summary className="small muted" style={{ cursor: 'pointer' }}>See the steps</summary><pre className="docview" style={{ maxHeight: 'none', whiteSpace: 'pre-wrap' }}>{b.steps}</pre></details>
            <div className="actions">
              <button className="btn" disabled={added[b.id]} onClick={() => addPlaybook(b, null)}>{added[b.id] ? 'Added for everyone' : 'Add for everyone'}</button>
              {people.length > 0 && <select aria-label="Add for one person" defaultValue="" onChange={(e) => { if (e.target.value) { addPlaybook(b, +e.target.value); e.target.value = '' } }}><option value="">For one person…</option>{people.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>}
            </div>
          </article>
        ))}
      </div>

      {use && (
        <div className="modal-bg" onClick={() => setUse(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={`Start ${use.name}`}>
            <div className="modal-h"><h3>{use.name}</h3><button className="icon-x" onClick={() => setUse(null)} aria-label="Close">✕</button></div>
            <div className="modal-b form-grid">
              <label className="f">Name<input value={use.name} onChange={(e) => setUse({ ...use, name: e.target.value })} /></label>
              <label className="f">Goal<textarea rows="2" value={use.goal} onChange={(e) => setUse({ ...use, goal: e.target.value })} /></label>
              {people.length === 0 ? <p className="warn">Add some employees first, then start a project.</p> : (
                <div className="f">Who is on it<div className="pickrow">{people.map((a) => (
                  <label key={a.id} className={`pick ${who.includes(a.id) ? 'on' : ''}`}><input type="checkbox" checked={who.includes(a.id)} onChange={(e) => setWho(e.target.checked ? [...who, a.id] : who.filter((x) => x !== a.id))} /><Avatar agent={a} size={26} /> {a.name}</label>
                ))}</div></div>
              )}
              <p className="small muted">These tasks are created and given to the best fit: {use.tasks.join(' · ')}.</p>
              <div className="actions"><button className="btn solid" disabled={!who.length || !use.name.trim() || !!busy} onClick={startProject}>{busy ? 'Creating…' : 'Create project'}</button><button className="btn" onClick={() => setUse(null)}>Cancel</button></div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
