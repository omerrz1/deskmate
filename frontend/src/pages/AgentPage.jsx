import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, usePoll } from '../api'
import Avatar from '../Avatar'
import Icon from '../Icon'
import { Skeleton, useTitle } from '../ui'
import Computer from './Computer'
import Overview from './agent/Overview'
import History from './agent/History'
import Train from './agent/Train'
import AgentSettings from './agent/AgentSettings'

const TABS = [['overview', 'Overview'], ['computer', 'Computer'], ['history', 'History'], ['train', 'Teach'], ['settings', 'Settings']]

export default function AgentPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'overview'
  const [a, reload] = usePoll(`/agents/${id}/overview`, 6000, [id])
  useTitle(a?.name)
  if (!a) return <div className="page"><Skeleton rows={5} /></div>
  const meet = async () => { const m = await api.post('/meetings', { agent_ids: [a.id] }); nav(`/meeting/${m.id}`) }
  const tabs = <div className="tabs">{TABS.map(([k, n]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setSp({ tab: k })}>{n}</button>)}</div>
  const actions = <div className="actions"><Link className="btn" to={`/team-chat?agent=${a.id}`}>Chat</Link><button className="btn solid" onClick={meet}>Talk</button></div>
  if (tab === 'computer') {
    // the computer fills the window: one slim bar above it says who it belongs to and lets you go elsewhere
    return (
      <div className="page wide computer-page">
        <header className="agent-bar">
          <Link to="/" className="agent-back" aria-label="Back to the team" title="Back to the team"><Icon name="back" size={18} /></Link>
          <Avatar agent={a} size={34} />
          <div className="agent-bar-name"><h1>{a.name}{a.is_manager ? <span className="tag mgr">Manager</span> : null}</h1><small>{a.role}</small></div>
          {tabs}
          {actions}
        </header>
        <Computer a={a} startFull={sp.get('full') === '1'} />
      </div>
    )
  }
  return (
    <div className="page">
      <Link to="/" className="back"><Icon name="back" size={14} /> Team</Link>
      <header className="page-h">
        <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
          <Avatar agent={a} size={44} />
          <div>
            <h1>{a.name}{a.is_manager ? <span className="tag mgr">Manager</span> : null}</h1>
            <p className="muted small">{a.role}{a.manager ? ` · reports to ${a.manager.name}` : ''}</p>
          </div>
        </div>
        {actions}
      </header>
      {tabs}
      {tab === 'overview' && <Overview a={a} go={(k) => setSp({ tab: k })} />}
      {tab === 'history' && <History a={a} />}
      {tab === 'train' && <Train a={a} />}
      {tab === 'settings' && <AgentSettings a={a} reload={reload} />}
    </div>
  )
}
