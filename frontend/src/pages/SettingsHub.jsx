import { useState } from 'react'
import { Link, NavLink, useParams } from 'react-router-dom'
import { api, usePoll } from '../api'
import { PageHeader, useTitle } from '../ui'
import Account from './Account'
import { useAuth } from '../Auth'
import CallingSettings from './Settings'
import FileManager from '../FileManager'
import { MailSettings } from './Office'
import Connections from './settings/Connections'
import Audit from './settings/Audit'
import BrowserSettings from './settings/Browser'
import Platform from './settings/Platform'
import Backups from './settings/Backups'
import Notifications from './settings/Notifications'
import Safety from './settings/Safety'
import Spend from './settings/Spend'
import ChatsSettings from './settings/ChatsSettings'

const GROUPS = [
  ['Workspace', [['company', 'Company'], ['connections', 'Connections'], ['email', 'Email'], ['files', 'Files']]],
  ['Work', [['calling', 'Calling'], ['chats', 'Chats and WhatsApp'], ['costs', 'Limits and spend'], ['browser', 'Browser']]],
  ['Safety', [['safety', 'Email rules'], ['audit', 'Audit log'], ['backups', 'Backups'], ['account', 'Account and sign-in']]],
  ['Alerts', [['notifications', 'Notifications']]],
]
const PLATFORM = ['Platform', [['platform', 'All workspaces']]]
const TITLES = Object.fromEntries([...GROUPS, PLATFORM].flatMap(([, items]) => items))

export default function SettingsHub() {
  const { tab = 'company' } = useParams()
  const { platform } = useAuth()
  useTitle('Settings')
  const groups = platform ? [...GROUPS, PLATFORM] : GROUPS
  return (
    <div className="page wide">
      <PageHeader title="Settings" sub={TITLES[tab] ? `${TITLES[tab]}` : 'Company details, calling, email, limits and your account.'} />
      <div className="set-layout">
        <nav className="set-nav" aria-label="Settings sections">
          {groups.map(([g, items]) => (
            <div key={g}><span className="side-label">{g}</span>
              {items.map(([k, n]) => <NavLink key={k} to={`/settings/${k}`} className={tab === k ? 'on' : ''} aria-current={tab === k ? 'page' : undefined}>{n}</NavLink>)}
            </div>
          ))}
        </nav>
        <div className="set-body">
          {tab === 'company' && <><CompanyForm /><DangerZone /><CloseWorkspace /></>}
          {tab === 'connections' && <Connections />}
          {tab === 'calling' && <Calling />}
          {tab === 'chats' && <ChatsSettings />}
          {tab === 'email' && <MailSettings />}
          {tab === 'costs' && <><Spend /><WorkLimits /><Costs /></>}
          {tab === 'safety' && <Safety />}
          {tab === 'browser' && <BrowserSettings />}
          {tab === 'notifications' && <Notifications />}
          {tab === 'backups' && <Backups />}
          {tab === 'audit' && <Audit />}
          {tab === 'platform' && platform && <Platform />}
          {tab === 'files' && <><h3 style={{ marginBottom: 10 }}>Shared drive</h3><FileManager owner="shared" /></>}
          {tab === 'account' && <Account embedded />}
        </div>
      </div>
    </div>
  )
}

function Calling() {
  const [c, reload] = usePoll('/central', 0)
  return (
    <>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Phone calling</h3>
        <label className="check"><input type="checkbox" checked={!c || c.calls_enabled} onChange={async (e) => { await api.put('/central', { calls_enabled: e.target.checked }); reload() }} /> Everyone on the team can make phone calls</label>
        <label>Calls from customer chats
          <select value={c?.call_route || 'whatsapp_then_phone'} onChange={async (e) => { await api.put('/central', { call_route: e.target.value }); reload() }}>
            <option value="whatsapp_then_phone">WhatsApp call through Twilio, then phone fallback</option>
            <option value="whatsapp">WhatsApp call only (Twilio)</option>
            <option value="phone">Regular phone call through Twilio</option>
          </select>
          <small>Campaign calls use Twilio Voice to call the customer's phone number.</small>
        </label>
        <div className="linkgrid">
          <Link className="linkcard" to="/data"><b>Customers</b><small>Upload and browse your customer list</small></Link>
          <Link className="linkcard" to="/campaigns"><b>Campaigns</b><small>Who gets called and why</small></Link>
          <Link className="linkcard" to="/calls"><b>Calls</b><small>Transcripts and summaries</small></Link>
          <Link className="linkcard" to="/knowledge"><b>Knowledge</b><small>What callers know about your business</small></Link>
          <Link className="linkcard" to="/overview"><b>Call numbers</b><small>Answer rate, outcomes, trends</small></Link>
          <Link className="linkcard" to="/activity"><b>System log</b><small>Everything that happened</small></Link>
        </div>
      </div></section>
      <LiveHelp c={c} reload={reload} />
      <CallingSettings embedded />
    </>
  )
}

function Costs() {
  const [c] = usePoll('/central', 0)
  if (!c) return null
  const M = c.models
  return (
    <section className="panel"><div className="panel-b form-grid">
      <h3>What costs money</h3>
      <p className="small"><b>{c.elevenlabs_rule}</b></p>
      <table className="costtable"><tbody>
        <tr><td>Day-to-day work (email, to-dos, reports, projects, training)</td><td><code>{M.agent_work}</code></td><td className="muted small">Small, cheap text model</td></tr>
        <tr><td>Live voice in meetings</td><td><code>{M.meetings_voice}</code></td><td className="muted small">Mini voice model</td></tr>
        <tr><td>Summaries and minutes</td><td><code>{M.summaries_and_reports}</code></td><td className="muted small">Small, cheap text model</td></tr>
        <tr><td>Phone calls</td><td><code>{M.phone_calls}</code></td><td className="muted small">The only place a premium voice is used</td></tr>
      </tbody></table>
      <p className="muted small">Calls use the voice engine chosen under Calling. Everything else never touches ElevenLabs, so its credits are kept for calls.</p>
    </div></section>
  )
}

export function CompanyForm({ onDone }) {
  const [c, reload] = usePoll('/company', 0)
  const [f, setF] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const [learned, setLearned] = useState('')
  if (c && !f) setF({ name: c.name, about: c.about, website: c.website, timezone: c.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone })
  if (!c || !f) return null
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const save = async () => { setMsg(''); try { await api.put('/company', f); setMsg('Saved.'); reload(); onDone && onDone() } catch (e) { setMsg(e.message) } }
  const learn = async () => {
    setMsg(''); setBusy('learn'); setLearned('')
    try { await api.put('/company', f); const r = await api.post('/company/learn', { url: f.website }); setLearned(`Read ${r.pages} page${r.pages === 1 ? '' : 's'} and made ${r.sections} knowledge sections. Your callers can now use them.`); if (r.about && !f.about) setF((x) => ({ ...x, about: r.about })); reload() }
    catch (e) { setMsg(e.message) }
    setBusy('')
  }
  return (
    <section className="panel"><div className="panel-b form-grid">
      <h3>Your company</h3>
      <p className="muted small">Name, description, website and time zone are shared by every workspace of {c.shared ? (c.company_workspace || 'your company') : 'your company'}: change them once and all of them, and their callers, use the new details.{c.shared && !c.can_edit && ' Only a company admin can change them, so they are read-only here.'}</p>
      <fieldset disabled={!c.can_edit} style={{ border: 0, padding: 0, margin: 0, display: 'contents' }}>
      <label className="f">Company name<input value={f.name} onChange={set('name')} placeholder="e.g. Acme Plumbing" /></label>
      <label className="f">What does the company do?<textarea rows="4" value={f.about} onChange={set('about')} placeholder="A few sentences: who you serve, what you sell, how you work. Your team reads this before every task." /></label>
      <label className="f">Website (optional)<input value={f.website} onChange={set('website')} placeholder="https://yourcompany.com" /></label>
      <div className="actions">
        <button className="btn" disabled={!f.website.trim() || !!busy} onClick={learn}>{busy === 'learn' ? 'Reading your website…' : 'Read my website into the knowledge base'}</button>
      </div>
      {learned && <p className="ok small">{learned}</p>}
      <label className="f">Time zone<input list="tzs" value={f.timezone} onChange={set('timezone')} /><datalist id="tzs">{c.timezones.map((z) => <option key={z} value={z} />)}</datalist></label>
      {msg && <p className={msg === 'Saved.' ? 'ok small' : 'warn small'}>{msg}</p>}
      <div className="actions"><button className="btn solid" disabled={!f.name.trim()} onClick={save}>Save</button></div>
      </fieldset>
    </div></section>
  )
}

function CloseWorkspace() {
  const { isOwner, workspace } = useAuth()
  const [c] = usePoll('/company', 0)
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ confirm: '', password: '' })
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  if (!isOwner || !workspace || workspace.id === 'main') return null
  const name = (c && c.name) || 'DELETE'
  const go = async () => {
    setMsg(''); setBusy(true)
    try { await api.post('/workspace/delete', { confirm: f.confirm, password: f.password }); window.location.href = '/' } catch (e) { setMsg(e.message); setBusy(false) }
  }
  return (
    <section className="panel danger"><div className="panel-b form-grid">
      <h3>Close this account</h3>
      <p className="muted small">Deletes your whole workspace for good: the team, files, apps, keys, settings and every sign-in. This cannot be undone. Download a backup first (Settings → Backups) if you may want it later.</p>
      {!open ? <div className="actions"><button className="btn danger" onClick={() => setOpen(true)}>Close my account…</button></div> : (
        <>
          <label className="f"><span>Type <b>{name}</b> to confirm</span><input autoComplete="off" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} /></label>
          <label className="f">Your password<input type="password" autoComplete="current-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
          {msg && <p className="warn small" role="alert">{msg}</p>}
          <div className="actions"><button className="btn danger" disabled={busy || f.confirm.trim() !== name || !f.password} onClick={go}>{busy ? 'Closing…' : 'Delete my account'}</button><button className="btn" onClick={() => { setOpen(false); setF({ confirm: '', password: '' }); setMsg('') }}>Cancel</button></div>
        </>
      )}
    </div></section>
  )
}

function DangerZone() {
  const { isOwner, logout } = useAuth()
  const [c] = usePoll('/company', 0)
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ confirm: '', password: '' })
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  if (!isOwner) return null
  const name = (c && c.name) || 'DELETE'
  const go = async () => {
    setMsg(''); setBusy(true)
    try { const r = await api.post('/company/delete', { confirm: f.confirm, password: f.password, keep_backup: true }); setDone(r) } catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  if (done) return (
    <section className="panel"><div className="panel-b form-grid">
      <h3>Company deleted</h3>
      <p className="muted small">Everything was removed: employees, customers, calls, files, connected apps and passwords for outside services. Admin accounts were kept.{done.backup ? <> A safety copy of the old data is on the server at <code>{done.backup}</code>. Delete it when you are sure you don't need it.</> : null}</p>
      <div className="actions"><button className="btn solid" onClick={() => { window.location.href = '/' }}>Start a new company</button></div>
    </div></section>
  )
  return (
    <section className="panel danger"><div className="panel-b form-grid">
      <h3>Delete this company</h3>
      <p className="muted small">Removes the team, customers, calls, projects, files and connected apps. Admin accounts stay. A backup is saved first.</p>
      {!open ? <div className="actions"><button className="btn danger" onClick={() => setOpen(true)}>Delete company…</button></div> : (
        <>
          <label className="f"><span>Type <b>{name}</b> to confirm</span><input autoComplete="off" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} /></label>
          <label className="f">Your password<input type="password" autoComplete="current-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
          {msg && <p className="warn small">{msg}</p>}
          <div className="actions">
            <button className="btn danger" disabled={busy || f.confirm.trim() !== name || !f.password} onClick={go}>{busy ? 'Deleting…' : 'Delete everything'}</button>
            <button className="btn" onClick={() => { setOpen(false); setF({ confirm: '', password: '' }); setMsg('') }}>Cancel</button>
          </div>
        </>
      )}
    </div></section>
  )
}

function LiveHelp({ c, reload }) {
  const [f, setF] = useState(null)
  const [msg, setMsg] = useState('')
  if (c && !f) setF({ admin_phone: c.admin_phone || '', live_question_timeout: c.live_question_timeout || 75, discuss_email: c.discuss_email !== false })
  if (!c || !f) return null
  const save = async () => { setMsg(''); try { await api.put('/central/live', { ...f, live_question_timeout: +f.live_question_timeout }); setMsg('Saved.'); reload() } catch (e) { setMsg(e.message) } }
  return (
    <section className="panel"><div className="panel-b form-grid">
      <h3>When a customer is on hold and needs you</h3>
      <p className="muted small">If a caller asks something an employee doesn't know, they put the customer on hold and ask you. You get a red bar in the app and an email, and a text if you add your number (needs Twilio).</p>
      <label className="f">Your mobile number (optional, for a text alert)<input value={f.admin_phone} onChange={(e) => setF({ ...f, admin_phone: e.target.value })} placeholder="+44 7700 900123" /></label>
      <label className="f">How long should they wait for you before telling the customer someone will call back? (seconds)<input type="number" min="20" max="240" value={f.live_question_timeout} onChange={(e) => setF({ ...f, live_question_timeout: e.target.value })} /></label>
      <label className="check"><input type="checkbox" checked={f.discuss_email} onChange={(e) => setF({ ...f, discuss_email: e.target.checked })} /> Email me a reminder when something has been on their discussion list for a while</label>
      {msg && <p className={msg === 'Saved.' ? 'ok small' : 'warn small'}>{msg}</p>}
      <div className="actions"><button className="btn solid" onClick={save}>Save</button></div>
    </div></section>
  )
}

function WorkLimits() {
  const [c, reload] = usePoll('/central', 0)
  const [f, setF] = useState(null)
  const [msg, setMsg] = useState('')
  if (c && !f) setF({ max_tasks_per_day: c.max_tasks_per_day, company_tasks_per_day: c.company_tasks_per_day, work_pace_seconds: c.work_pace_seconds })
  if (!c || !f) return null
  const save = async () => { setMsg(''); try { await api.put('/central', { max_tasks_per_day: +f.max_tasks_per_day, company_tasks_per_day: +f.company_tasks_per_day, work_pace_seconds: +f.work_pace_seconds }); setMsg('Saved.'); reload() } catch (e) { setMsg(e.message) } }
  return (
    <section className="panel"><div className="panel-b form-grid">
      <h3>How your team works</h3>
      <p className="muted small">Every task goes through the same steps: it is asked for in a meeting, lands on a to-do list, the employee writes a plan, then works through the plan one step at a time and hands over the result. These limits keep a busy team from running up costs.</p>
      <div className="row2">
        <label className="f">Pause between steps (seconds)<input type="number" min="0" max="600" value={f.work_pace_seconds} onChange={(e) => setF({ ...f, work_pace_seconds: e.target.value })} /><small className="muted">0 = no pause. Tasks you start with “do it now” never pause.</small></label>
        <label className="f">Tasks per employee per day<input type="number" min="1" max="500" value={f.max_tasks_per_day} onChange={(e) => setF({ ...f, max_tasks_per_day: e.target.value })} /></label>
      </div>
      <label className="f">Tasks for the whole company per day<input type="number" min="1" max="2000" value={f.company_tasks_per_day} onChange={(e) => setF({ ...f, company_tasks_per_day: e.target.value })} /><small className="muted">{c.tasks_today} started in the last 24 hours. At the limit, work carries on the next day.</small></label>
      <label className="check"><input type="checkbox" checked={!!c.work_paused} onChange={async (e) => { await api.put('/central', { work_paused: e.target.checked }); reload() }} /> Pause all work (nobody starts or continues a task until you switch this off)</label>
      {msg && <p className={msg === 'Saved.' ? 'ok small' : 'warn small'}>{msg}</p>}
      <div className="actions"><button className="btn solid" onClick={save}>Save</button></div>
    </div></section>
  )
}
