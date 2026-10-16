import { usePoll } from '../api'
import Avatar from '../Avatar'
import PersonAvatar, { AiTag, RoleBadge } from '../PersonAvatar'
import { PageHeader, Skeleton, useTitle } from '../ui'

/** Who is who, for members: the people you work with, and the AI team. The two are kept clearly apart. */
export default function Directory() {
  useTitle('Who is who')
  const [people] = usePoll('/people/directory', 0)
  const [team] = usePoll('/agents', 6000)
  return (
    <div className="page dir-page">
      <PageHeader title="Who is who" sub="The people you work with, and the AI employees on the team." />
      <section className="pp-card">
        <header><h3>People <i>{people ? people.length : ''}</i></h3><span className="faint small">They sign in like you</span></header>
        {!people ? <Skeleton rows={2} /> : people.map((p) => (
          <div key={p.id} className="dir-row"><PersonAvatar name={p.name} size={38} /><div className="pp-who"><b>{p.name}</b><small>{p.title || ' '}</small></div><RoleBadge role={p.role} /></div>
        ))}
      </section>
      <section className="pp-card">
        <header><h3>AI team <i>{team ? team.length : ''}</i></h3><span className="faint small">AI employees: they do work for the company</span></header>
        {!team ? <Skeleton rows={2} /> : team.length === 0 ? <p className="faint small" style={{ padding: 16 }}>No AI employees yet.</p> : team.map((a) => (
          <div key={a.id} className="dir-row"><Avatar agent={a} size={38} /><div className="pp-who"><b>{a.name} <AiTag /></b><small>{a.role || 'AI employee'}</small></div><span className={`dir-state ${a.working ? 'live' : ''}`}>{a.working ? `Working: ${a.current_task || ''}` : 'Idle'}</span></div>
        ))}
      </section>
    </div>
  )
}
