import { Component, lazy, Suspense, useEffect, useState } from 'react'
import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api, usePoll } from './api'
import { AuthGate, JoinPage, useAuth } from './Auth'
import Icon from './Icon'
import { FeedbackProvider, useToast } from './Feedback'
import LiveHelpBanner from './LiveHelpBanner'
import CommandPalette from './CommandPalette'
import SetupGate from './SetupGate'
import { useTheme } from './theme'
import Avatar from './Avatar'
import PersonAvatar from './PersonAvatar'
import { Skeleton } from './ui'
import { SITE_PAGES } from './site/routes'

const Site = lazy(() => import('./site/Site'))
const Home = lazy(() => import('./pages/Home'))
const Projects = lazy(() => import('./pages/Projects'))
const Apps = lazy(() => import('./pages/Apps'))
const AgentPage = lazy(() => import('./pages/AgentPage'))
const Project = lazy(() => import('./pages/Project'))
const Scorecards = lazy(() => import('./pages/Scorecards'))
const Templates = lazy(() => import('./pages/Templates'))
const Inbox = lazy(() => import('./pages/Inbox'))
const Chats = lazy(() => import('./pages/Chats'))
const SettingsHub = lazy(() => import('./pages/SettingsHub'))
const Meeting = lazy(() => import('./pages/Meeting'))
const MockPhone = lazy(() => import('./pages/MockPhone'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Data = lazy(() => import('./pages/Data'))
const Campaigns = lazy(() => import('./pages/Campaigns'))
const Calls = lazy(() => import('./pages/Calls'))
const Clients = lazy(() => import('./pages/Clients'))
const Activity = lazy(() => import('./pages/Activity'))
const Knowledge = lazy(() => import('./pages/Knowledge'))
const People = lazy(() => import('./pages/People'))
const Files = lazy(() => import('./pages/Files'))
const MyWork = lazy(() => import('./pages/MyWork'))
const Directory = lazy(() => import('./pages/Directory'))
const Account = lazy(() => import('./pages/Account'))

const LEGACY = ['/data', '/campaigns', '/calls', '/clients', '/knowledge', '/overview', '/activity']
const TITLES = { '/': 'Team', '/performance': 'Performance', '/templates': 'Templates', '/projects': 'Projects', '/apps': 'Apps', '/inbox': 'Inbox', '/chats': 'Chats', '/settings': 'Settings', '/people': 'People', '/files': 'Files' }

class Boundary extends Component {
  state = { err: null }
  static getDerivedStateFromError(err) { return { err } }
  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="page" style={{ paddingTop: 60 }}>
        <h1>Something went wrong</h1>
        <p className="muted" style={{ margin: '8px 0 18px' }}>This page hit an unexpected error. Your data is safe.</p>
        <div className="actions"><button className="btn solid" onClick={() => window.location.reload()}>Reload the page</button><Link className="btn" to="/" onClick={() => this.setState({ err: null })}>Back to the team</Link></div>
        <details style={{ marginTop: 18 }}><summary className="muted small" style={{ cursor: 'pointer' }}>Details</summary><pre className="docview">{String(this.state.err?.stack || this.state.err)}</pre></details>
      </div>
    )
  }
}

function NotFound() {
  return (
    <div className="page" style={{ paddingTop: 60 }}>
      <h1>Page not found</h1>
      <p className="muted" style={{ margin: '8px 0 18px' }}>That page doesn't exist, or it has moved.</p>
      <Link className="btn solid" to="/">Back to the team</Link>
    </div>
  )
}

function Shell() {
  const [health] = usePoll('/status', 15000)
  const [att] = usePoll('/attention', 8000)
  const [company] = usePoll('/company', 0)
  const [central, reloadCentral] = usePoll('/central', 10000)
  const [team] = usePoll('/agents', 6000)
  const [palette, setPalette] = useState(false)
  const [theme, setTheme] = useTheme()
  const auth = useAuth()
  const toast = useToast()
  const loc = useLocation()
  const legacy = LEGACY.some((p) => loc.pathname.startsWith(p))
  const badge = att ? att.unread + att.approvals + att.meetings.length : 0
  const on = (p) => (p === '/' ? loc.pathname === '/' || loc.pathname.startsWith('/agents') : loc.pathname.startsWith(p))
  useEffect(() => { const t = TITLES['/' + loc.pathname.split('/')[1]] || (legacy ? 'Settings' : null); if (t && !/^\/(agents|projects)\/\d+/.test(loc.pathname)) document.title = `${t} · Deskmates` }, [loc.pathname, legacy])
  const paused = !!central?.work_paused
  const flip = async () => { try { await api.put('/central', { work_paused: !paused }); reloadCentral(); toast.ok(paused ? 'The team is working again' : 'Work paused') } catch (e) { toast.err(e.message) } }
  useEffect(() => {  // ⌘K / Ctrl+K opens search from anywhere; "/" does too when you are not typing
    const k = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((v) => !v) }
      else if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setPalette(true) }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])
  const section = legacy ? 'Settings' : TITLES['/' + loc.pathname.split('/')[1]] || (loc.pathname.startsWith('/agents') ? 'Team' : '')
  const crew = (team || []).filter((a) => a.is_manager)  // the sidebar lists managers only; everyone else is on the Team page
  const link = (to, icon, label, count) => <NavLink to={to} className={on(to) ? 'on' : ''}><span><Icon name={icon} size={17} />{label}</span>{count > 0 && <i className="badge">{count}</i>}</NavLink>
  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="brand"><span className="mark"><i /></span><span className="t"><b>Deskmates</b>{company?.name && <small>{company.name}</small>}</span></Link>
        <nav className="nav" aria-label="Main">
          {link('/', 'users', 'Team')}
          {link('/people', 'person', 'People')}
          {link('/projects', 'projects', 'Projects')}
          {link('/files', 'folder', 'Files')}
          {link('/inbox', 'inbox', 'Inbox', badge)}
          {link('/chats', 'chat', 'Chats', att ? att.chats_unread || 0 : 0)}
          {link('/apps', 'apps', 'Apps')}
          {link('/settings', 'sliders', 'Settings')}
        </nav>
        {crew.length > 0 && (
          <div className="side-team" aria-label="Managers">
            <span className="side-label">{crew.length === 1 ? 'Manager' : 'Managers'}</span>
            {crew.map((a) => (
              <NavLink key={a.id} to={`/agents/${a.id}`} className={loc.pathname === `/agents/${a.id}` ? 'on' : ''}>
                <Avatar agent={a} size={20} />
                <span className="nm">{a.name}</span>
                <i className={`dot ${a.working ? 'live' : ''}`} title={a.working ? `Working: ${a.current_task || ''}` : 'Idle'} />
              </NavLink>
            ))}
          </div>
        )}
        <div className="side-foot">
          {health && !health.openai && <div className="mode"><Icon name="alert" size={14} /> No API key set</div>}
          {central && <button className={`pause ${paused ? 'paused' : ''}`} onClick={flip} title={paused ? 'Resume the team' : 'Pause all work'}><i className={`dot ${paused ? '' : 'ok'}`} />{paused ? 'Paused' : 'Team working'}<Icon name={paused ? 'play' : 'pause'} size={13} /></button>}
          <div className="who-row"><PersonAvatar name={auth.name || auth.username} size={26} /><span className="u-n">{auth.name || auth.username}</span><button onClick={auth.logout} title="Sign out" aria-label="Sign out"><Icon name="logout" size={16} /></button></div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="crumb"><span>{company?.name || 'Deskmates'}</span>{section && <><i>/</i><b>{section}</b></>}</div>
          <button className="search-trigger" onClick={() => setPalette(true)} ><Icon name="search" size={14} /><span>Search or jump to…</span><kbd>⌘K</kbd></button>
          <Link to="/settings/account" className="bell shell-mobile-only" aria-label="Your account" title="Your account"><span className="u-av" style={{ width: 28, height: 28, fontSize: 12 }}>{(auth.username || '?').slice(0, 1).toUpperCase()}</span></Link>
          <button className="bell" onClick={() => setTheme(theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system')} title={`Theme: ${theme === 'system' ? 'matches your computer' : theme}. Click to change.`} aria-label={`Theme: ${theme}. Click to change.`}><Icon name={theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'monitor'} size={17} /></button>
          <Link to="/inbox" className={`bell ${badge > 0 ? 'has' : ''}`} aria-label={badge > 0 ? `${badge} items need you` : 'Inbox'} title={badge > 0 ? `${badge} need you` : 'Inbox'}><Icon name="inbox" size={17} />{badge > 0 && <i>{badge > 99 ? '99+' : badge}</i>}</Link>
        </header>
        <div className={`content ${legacy ? 'legacy' : ''}`}>
        {legacy && <Link to="/settings/calling" className="back"><Icon name="back" size={14} /> Settings</Link>}
        <Boundary key={loc.pathname}>
          <Suspense fallback={<div className="page"><Skeleton rows={4} /></div>}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/agents/:id" element={<AgentPage />} />
            <Route path="/performance" element={<Scorecards />} />
            <Route path="/templates" element={<Templates />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:id" element={<Project />} />
            <Route path="/people" element={<People />} />
            <Route path="/files" element={<Files />} />
            <Route path="/apps" element={<Apps />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/chats" element={<Chats />} />
            <Route path="/settings" element={<SettingsHub />} />
            <Route path="/settings/connectors" element={<Navigate to={`/apps${loc.search}`} replace />} />
            <Route path="/settings/:tab" element={<SettingsHub />} />
            <Route path="/overview" element={<Dashboard health={health} />} />
            <Route path="/data" element={<Data />} />
            <Route path="/campaigns" element={<Campaigns />} />
            <Route path="/calls" element={<Calls />} />
            <Route path="/clients" element={<Clients />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/knowledge" element={<Knowledge />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
          </Suspense>
        </Boundary>
        </div>
      </main>
      <CommandPalette open={palette} onClose={() => setPalette(false)} paused={paused} onTogglePause={flip} />
    </div>
  )
}

/** What a member sees: their own projects and files, who is who, and their account. Nothing else is reachable, and the server refuses it too. */
function MemberShell() {
  const auth = useAuth()
  const loc = useLocation()
  const [theme, setTheme] = useTheme()
  const on = (p) => (p === '/' ? loc.pathname === '/' : loc.pathname.startsWith(p))
  const link = (to, icon, label) => <NavLink to={to} className={on(to) ? 'on' : ''}><span><Icon name={icon} size={17} />{label}</span></NavLink>
  const section = { '/': 'My work', '/projects': 'Projects', '/files': 'Files', '/team': 'Who is who', '/account': 'Account' }['/' + loc.pathname.split('/')[1]] || ''
  return (
    <div className="shell member-shell">
      <aside className="sidebar">
        <Link to="/" className="brand"><span className="mark"><i /></span><span className="t"><b>Deskmates</b>{auth.workspace?.name && <small>{auth.workspace.name}</small>}</span></Link>
        <nav className="nav" aria-label="Main">
          {link('/', 'person', 'My work')}
          {link('/projects', 'projects', 'Projects')}
          {link('/files', 'folder', 'Files')}
          {link('/team', 'users', 'Who is who')}
          {link('/account', 'lock', 'Account')}
        </nav>
        <div className="side-foot">
          <div className="who-row"><PersonAvatar name={auth.name || auth.username} size={26} /><span className="u-n">{auth.name || auth.username}<small>Member</small></span><button onClick={auth.logout} title="Sign out" aria-label="Sign out"><Icon name="logout" size={15} /></button></div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="crumb"><span>{auth.workspace?.name || 'Deskmates'}</span>{section && <><i>/</i><b>{section}</b></>}</div>
          <button className="bell" onClick={() => setTheme(theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system')} title={`Theme: ${theme === 'system' ? 'matches your computer' : theme}. Click to change.`} aria-label="Change theme"><Icon name={theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'monitor'} size={16} /></button>
        </header>
        <div className="content">
          <Boundary key={loc.pathname}>
            <Suspense fallback={<div className="page"><Skeleton rows={4} /></div>}>
              <Routes>
                <Route path="/" element={<MyWork />} />
                <Route path="/projects" element={<Projects />} />
                <Route path="/projects/:id" element={<Project />} />
                <Route path="/files" element={<Files />} />
                <Route path="/team" element={<Directory />} />
                <Route path="/account" element={<Account />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </Boundary>
        </div>
      </main>
    </div>
  )
}

/** Members get their own frame. Everyone else gets the admin app, behind the step that connects the company's AI keys. */
function RoleSplit() {
  const auth = useAuth()
  if (auth.isMember) return <MemberShell />
  return (
    <SetupGate>
      <LiveHelpBanner />
      <Suspense fallback={<div className="page"><Skeleton rows={4} /></div>}>
        <Routes>
          <Route path="/softphone" element={<MockPhone />} />
          <Route path="/website-demo" element={<MockPhone website />} />
          <Route path="/meeting/:id" element={<Meeting />} />
          <Route path="*" element={<Shell />} />
        </Routes>
      </Suspense>
    </SetupGate>
  )
}

export default function App() {
  const { pathname } = useLocation()
  const page = SITE_PAGES[pathname.replace(/\/+$/, '')]
  // about, product, security, contact and the legal pages are open to everyone, signed in or not
  if (page) return <Suspense fallback={<div className="auth-wrap" aria-busy="true" />}><Site page={page} /></Suspense>
  const join = pathname.match(/^\/join\/([^/]+)\/?$/)  // an invitation link: open to anyone who has it
  if (join) return <JoinPage token={join[1]} />
  return (
    <FeedbackProvider>
      <AuthGate>
        <RoleSplit />
      </AuthGate>
    </FeedbackProvider>
  )
}
