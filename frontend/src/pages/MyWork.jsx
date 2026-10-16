import { Link } from 'react-router-dom'
import { ago, usePoll } from '../api'
import { useAuth } from '../Auth'
import Avatar from '../Avatar'
import Icon from '../Icon'
import PersonAvatar, { AiTag } from '../PersonAvatar'
import { EmptyState, Skeleton, useTitle } from '../ui'

const stateOf = (p) => (p.status === 'done' ? ['Finished', 'done'] : !p.total_tasks ? ['Planning', 'plan'] : p.doing_tasks > 0 ? ['In progress', 'live'] : p.open_tasks === 0 ? ['All done', 'done'] : ['Active', 'idle'])

/** The home page of a member: the projects they work on, who is on them, and where their files are. */
export default function MyWork() {
  useTitle('My work')
  const { name, workspace } = useAuth()
  const [projects] = usePoll('/projects', 8000)
  const first = (name || '').split(/\s+/)[0] || 'there'
  return (
    <div className="page wide mw-page">
      <header className="mw-hero">
        <div><h1>Hi {first}</h1><p className="muted">Here is what you are working on at {workspace?.name || 'your company'}, with the admins and the AI team.</p></div>
        <Link className="btn" to="/files"><Icon name="folder" size={14} /> Your files</Link>
      </header>
      {!projects ? <Skeleton rows={3} /> : projects.length === 0 ? (
        <EmptyState title="You are not on a project yet" hint="An admin adds you to the projects you work on. Until then there is nothing to show here." />
      ) : (
        <div className="mw-grid">
          {projects.map((p) => {
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
                  <span className="faces">
                    {p.people.slice(0, 4).map((m) => <PersonAvatar key={'p' + m.id} name={m.name} size={28} />)}
                    {p.members.slice(0, 4).map((m) => <Avatar key={'a' + m.id} agent={m} size={28} />)}
                  </span>
                  <span className="faint small">{p.people.length} {p.people.length === 1 ? 'person' : 'people'} <AiTag /> {p.members.length}</span>
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
