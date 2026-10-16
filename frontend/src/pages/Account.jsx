import { useState } from 'react'
import { api, usePoll, clock } from '../api'
import { Panel, Badge } from '../ui'
import { useAuth } from '../Auth'
import { Link } from 'react-router-dom'
import { useConfirm, useToast } from '../Feedback'
import PersonAvatar, { RoleBadge } from '../PersonAvatar'
import TwoFactor from './settings/TwoFactor'

const device = (ua) => {
  const b = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'Browser'
  const o = /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Linux/.test(ua) ? 'Linux' : ''
  return `${b}${o ? ' on ' + o : ''}`
}

export default function Account({ embedded = false }) {
  const { username, name, title, role, logout, isAdmin } = useAuth()
  const [pw, setPw] = useState({ current: '', new: '', again: '' })
  const [msg, setMsg] = useState({ ok: '', err: '' })
  const [sessions, reloadS] = usePoll('/auth/sessions', 0)
  const [log] = usePoll(isAdmin ? '/security/log?limit=40' : null, 15000)  // only admins read the security log and the company settings
  const [settings, reloadSet] = usePoll(isAdmin ? '/settings' : null, 0)
  const [days, setDays] = useState(null)

  const change = async (e) => {
    e.preventDefault(); setMsg({ ok: '', err: '' })
    if (pw.new !== pw.again) return setMsg({ ok: '', err: 'The new passwords do not match.' })
    try { await api.post('/auth/password', { current: pw.current, new: pw.new }); setPw({ current: '', new: '', again: '' }); setMsg({ ok: 'Password changed. Your other sessions were signed out.', err: '' }); reloadS() }
    catch (x) { setMsg({ ok: '', err: x.message }) }
  }
  const retention = days ?? settings?.transcript_retention_days ?? 0

  return (
    <div className={embedded ? "" : "page"}>
      {!embedded && <header className="page-h"><div><h1>Account & security</h1><p className="muted">Signed in as <b>{username}</b></p></div><button className="btn" onClick={logout}>Sign out</button></header>}
      <Profile name={name} title={title} role={role} email={username} isAdmin={isAdmin} />

      {embedded && <div className="row-split" style={{ marginBottom: 14, borderBottom: 0 }}><span>Signed in as <b>{username}</b></span><button className="btn" onClick={logout}>Sign out</button></div>}
      <div className="grid g-1-1">
        <TwoFactor />
        <Panel title="Change password">
          <form className="form" onSubmit={change}>
            <label>Current password<input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></label>
            <label>New password <span className="faint">(12+ characters)</span><input type="password" autoComplete="new-password" value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} /></label>
            <label>Repeat new password<input type="password" autoComplete="new-password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} /></label>
            {msg.err && <p className="warn">{msg.err}</p>}{msg.ok && <p className="ok">{msg.ok}</p>}
            <div className="actions"><button className="btn solid" disabled={!pw.current || !pw.new}>Change password</button></div>
          </form>
        </Panel>

        {isAdmin && <Panel title="Data retention">
          <p className="muted small">Calls are never recorded: only a written transcript and summary are kept. Delete those automatically after a set time. 0 keeps them forever.</p>
          <div className="actions" style={{ marginTop: 12 }}>
            <label className="inline-f">Delete call transcripts after <input type="number" min="0" max="3650" style={{ width: 90 }} value={retention} onChange={(e) => setDays(+e.target.value)} /> days</label>
            <button className="btn small" onClick={async () => { await api.put('/settings', { transcript_retention_days: retention }); setDays(null); reloadSet() }}>Save</button>
          </div>
          <p className="muted small" style={{ marginTop: 12 }}>To erase one person’s data (details and calls), open them under Customers → their page → “Delete all data”.</p>
        </Panel>}
      </div>

      <Panel title="Where you are signed in" pad={false}>
        <table className="tbl"><tbody>
          {(sessions || []).map((s) => (
            <tr key={s.id} style={{ cursor: 'default' }}>
              <td className="strong">{device(s.user_agent || '')}{s.current && <> <Badge kind="good">this device</Badge></>}<small>{s.ip}</small></td>
              <td className="num">last active {clock(s.last_seen)}</td>
              <td className="num">{!s.current && <button className="link" onClick={async () => { await api.del(`/auth/sessions/${s.id}`); reloadS() }}>Sign out</button>}</td>
            </tr>
          ))}
        </tbody></table>
      </Panel>

      {isAdmin && <Panel title="Security log" pad={false}>
        <table className="tbl"><tbody>
          {(log || []).map((r, i) => (
            <tr key={i} style={{ cursor: 'default' }}>
              <td className="num">{clock(r.ts)}</td>
              <td className="wide" style={{ width: 'auto' }}>{r.message}</td>
              <td className="num">{r.meta?.ip}</td>
            </tr>
          ))}
        </tbody></table>
      </Panel>}
    </div>
  )
}

/** Who you are to the rest of the company: the name and job title others see. */
function Profile({ name, title, role, email, isAdmin }) {
  const toast = useToast()
  const [f, setF] = useState({ name, title })
  const [busy, setBusy] = useState(false)
  const save = async (e) => {
    e.preventDefault(); setBusy(true)
    try { await api.patch('/me', { name: f.name, title: f.title }); toast.ok('Saved. Reload to see your new name everywhere.') } catch (x) { toast.err(x.message) } finally { setBusy(false) }
  }
  return (
    <Panel title="Your profile">
      <div className="acct-me"><PersonAvatar name={f.name || email} size={54} /><div><b>{name}</b><p className="muted small">{email}</p><RoleBadge role={role} /></div></div>
      <form className="form" onSubmit={save} style={{ marginTop: 14 }}>
        <div className="row2"><label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label><label>Job title <span className="faint">(optional)</span><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label></div>
        <div className="actions"><button className="btn solid" disabled={busy || !f.name.trim()}>Save</button>{isAdmin && <Link className="link" to="/people">Manage the people in your company</Link>}</div>
      </form>
    </Panel>
  )
}
