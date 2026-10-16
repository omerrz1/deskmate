import { useState } from 'react'
import { api, usePoll, clock } from '../api'
import { Panel, Badge } from '../ui'
import { useAuth } from '../Auth'
import { useConfirm } from '../Feedback'
import TwoFactor from './settings/TwoFactor'

const device = (ua) => {
  const b = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'Browser'
  const o = /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Linux/.test(ua) ? 'Linux' : ''
  return `${b}${o ? ' on ' + o : ''}`
}

export default function Account({ embedded = false }) {
  const { username, logout, isOwner } = useAuth()
  const [pw, setPw] = useState({ current: '', new: '', again: '' })
  const [msg, setMsg] = useState({ ok: '', err: '' })
  const [sessions, reloadS] = usePoll('/auth/sessions', 0)
  const [log] = usePoll('/security/log?limit=40', 15000)
  const [settings, reloadSet] = usePoll('/settings', 0)
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

        <Panel title="Data retention">
          <p className="muted small">Calls are never recorded: only a written transcript and summary are kept. Delete those automatically after a set time. 0 keeps them forever.</p>
          <div className="actions" style={{ marginTop: 12 }}>
            <label className="inline-f">Delete call transcripts after <input type="number" min="0" max="3650" style={{ width: 90 }} value={retention} onChange={(e) => setDays(+e.target.value)} /> days</label>
            <button className="btn small" onClick={async () => { await api.put('/settings', { transcript_retention_days: retention }); setDays(null); reloadSet() }}>Save</button>
          </div>
          <p className="muted small" style={{ marginTop: 12 }}>To erase one person’s data (details and calls), open them under Customers → their page → “Delete all data”.</p>
        </Panel>
      </div>

      <Admins isOwner={isOwner} />

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

      <Panel title="Security log" pad={false}>
        <table className="tbl"><tbody>
          {(log || []).map((r, i) => (
            <tr key={i} style={{ cursor: 'default' }}>
              <td className="num">{clock(r.ts)}</td>
              <td className="wide" style={{ width: 'auto' }}>{r.message}</td>
              <td className="num">{r.meta?.ip}</td>
            </tr>
          ))}
        </tbody></table>
      </Panel>
    </div>
  )
}

const PW_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
const makePassword = () => { const a = new Uint32Array(16); crypto.getRandomValues(a); return Array.from(a, (n) => PW_CHARS[n % PW_CHARS.length]).join('') }

function Admins({ isOwner }) {
  const confirm = useConfirm()
  const auth = useAuth()
  const [list, reload] = usePoll('/admins', 0)
  const [f, setF] = useState({ username: '', password: makePassword() })
  const [msg, setMsg] = useState({ ok: '', err: '' })
  const [made, setMade] = useState(null)  // the person just added, with the temporary password to hand over
  const [copied, setCopied] = useState(false)
  const [reset, setReset] = useState(null)  // { id, password }
  const note = (ok, err = '') => setMsg({ ok, err })
  const add = async (e) => {
    e.preventDefault(); note(''); setMade(null)
    try { const r = await api.post('/admins', f); setMade({ username: r.username, password: f.password }); setF({ username: '', password: makePassword() }); setCopied(false); reload() } catch (x) { note('', x.message) }
  }
  const doReset = async () => {
    note('')
    try { await api.put(`/admins/${reset.id}/password`, { password: reset.password }); setMade({ username: reset.name, password: reset.password, reset: true }); setCopied(false); setReset(null); reload() } catch (x) { note('', x.message) }
  }
  const remove = async (a) => {
    if (!(await confirm({ title: `Remove ${a.username}?`, body: 'They are signed out straight away and can no longer sign in to the company.', confirm: 'Remove', danger: true }))) return
    note(''); try { await api.del(`/admins/${a.id}`); reload() } catch (x) { note('', x.message) }
  }
  const company = auth.workspace?.name || 'the company'
  const invite = made ? `You have been added to ${company} on Deskmates.\nSign in at ${window.location.origin}\nEmail or username: ${made.username}\nTemporary password: ${made.password}\nYou will choose your own password the first time you sign in.` : ''
  const copy = async () => { try { await navigator.clipboard.writeText(invite); setCopied(true) } catch { setCopied(false) } }
  return (
    <Panel title="People who can sign in" pad={false}>
      <div className="panel-b">
        <p className="muted small">Add the people who work with you. They sign in with the email or username you give them and choose their own password the first time. Everyone here can run the whole company: your team, projects, connected apps and settings. {isOwner ? 'As the owner you can also reset or remove anyone, and delete the company.' : 'You can reset or remove the people you add; the owner can manage everyone.'}</p>
      </div>
      <table className="tbl"><tbody>
        {(list || []).map((a) => (
          <tr key={a.id} style={{ cursor: 'default' }}>
            <td className="strong">{a.username} {a.role === 'owner' && <Badge kind="good">owner</Badge>} {a.you && <Badge>you</Badge>}{a.must_change ? <small>has not signed in yet</small> : <small>{a.last_seen ? `last active ${clock(a.last_seen)}` : 'never signed in'}{a.added_by ? ` · added by ${a.added_by}` : ''}{a.totp_on ? ' · two-factor on' : ''}</small>}</td>
            <td className="num">
              {a.can_manage && <>
                <button className="link" onClick={() => setReset({ id: a.id, name: a.username, password: makePassword() })}>Reset password</button>{' · '}
                <button className="link" onClick={() => remove(a)}>Remove</button>
              </>}
            </td>
          </tr>
        ))}
      </tbody></table>
      {reset && (
        <div className="panel-b form">
          <label>New temporary password for {reset.name} <span className="faint">(12+ characters; they choose their own at next sign-in)</span><input type="text" autoComplete="off" value={reset.password} onChange={(e) => setReset({ ...reset, password: e.target.value })} /></label>
          <div className="actions"><button className="btn solid" disabled={reset.password.length < 12} onClick={doReset}>Set temporary password</button><button className="btn" onClick={() => setReset(null)}>Cancel</button></div>
        </div>
      )}
      <form className="panel-b form" onSubmit={add}>
        <h4 style={{ margin: 0 }}>Add a person</h4>
        <label>Email or username<input autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} placeholder="e.g. sam@yourcompany.com" /></label>
        <label>Temporary password <span className="faint">(made for you; they change it when they first sign in)</span>
          <span style={{ display: 'flex', gap: 8 }}><input type="text" autoComplete="off" style={{ flex: 1 }} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /><button type="button" className="btn" onClick={() => setF({ ...f, password: makePassword() })}>Make another</button></span></label>
        <div className="actions"><button className="btn solid" disabled={f.username.trim().length < 3 || f.password.length < 12}>Add person</button></div>
      </form>
      {made && (
        <div className="panel-b">
          <p className="ok" style={{ marginTop: 0 }}>{made.reset ? `A new temporary password is set for ${made.username}.` : `${made.username} can sign in now.`} Send them this. The password is shown only now.</p>
          <pre className="docview" style={{ whiteSpace: 'pre-wrap' }}>{invite}</pre>
          <div className="actions"><button className="btn" onClick={copy}>{copied ? 'Copied' : 'Copy message'}</button><button className="btn" onClick={() => setMade(null)}>Done</button></div>
        </div>
      )}
      {msg.err && <div className="panel-b"><p className="warn">{msg.err}</p></div>}
    </Panel>
  )
}
