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

function Admins({ isOwner }) {
  const confirm = useConfirm()
  const [list, reload] = usePoll('/admins', 0)
  const [f, setF] = useState({ username: '', password: '' })
  const [msg, setMsg] = useState({ ok: '', err: '' })
  const [reset, setReset] = useState(null)  // { id, password }
  const note = (ok, err = '') => setMsg({ ok, err })
  const add = async (e) => {
    e.preventDefault(); note('')
    try { await api.post('/admins', f); note(`${f.username} can sign in now. They will be asked to choose their own password.`); setF({ username: '', password: '' }); reload() } catch (x) { note('', x.message) }
  }
  const doReset = async () => {
    note('')
    try { await api.put(`/admins/${reset.id}/password`, { password: reset.password }); note('Temporary password set. They were signed out and must choose a new one.'); setReset(null); reload() } catch (x) { note('', x.message) }
  }
  const remove = async (a) => {
    if (!(await confirm({ title: `Remove ${a.username}?`, body: 'They are signed out straight away.', confirm: 'Remove', danger: true }))) return
    note(''); try { await api.del(`/admins/${a.id}`); reload() } catch (x) { note('', x.message) }
  }
  return (
    <Panel title="Admins" pad={false}>
      <div className="panel-b">
        <p className="muted small">Everyone here can run the whole company: your team, projects, connected apps and settings. Only the owner can add or remove admins, or delete the company.</p>
      </div>
      <table className="tbl"><tbody>
        {(list || []).map((a) => (
          <tr key={a.id} style={{ cursor: 'default' }}>
            <td className="strong">{a.username} {a.role === 'owner' && <Badge kind="good">owner</Badge>} {a.you && <Badge>you</Badge>}{a.must_change ? <small>has not chosen a password yet</small> : <small>{a.last_seen ? `last active ${clock(a.last_seen)}` : 'never signed in'}</small>}</td>
            <td className="num">
              {isOwner && a.role !== 'owner' && <>
                <button className="link" onClick={() => setReset({ id: a.id, name: a.username, password: '' })}>Reset password</button>{' · '}
                <button className="link" onClick={() => remove(a)}>Remove</button>
              </>}
            </td>
          </tr>
        ))}
      </tbody></table>
      {reset && (
        <div className="panel-b form">
          <label>Temporary password for {reset.name} <span className="faint">(12+ characters; they choose their own at next sign-in)</span><input type="text" autoComplete="off" value={reset.password} onChange={(e) => setReset({ ...reset, password: e.target.value })} /></label>
          <div className="actions"><button className="btn solid" disabled={reset.password.length < 12} onClick={doReset}>Set temporary password</button><button className="btn" onClick={() => setReset(null)}>Cancel</button></div>
        </div>
      )}
      {isOwner && (
        <form className="panel-b form" onSubmit={add}>
          <h4 style={{ margin: 0 }}>Add an admin</h4>
          <label>Username<input autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} placeholder="e.g. sam" /></label>
          <label>Temporary password <span className="faint">(12+ characters; tell them in person, they change it at first sign-in)</span><input type="text" autoComplete="off" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
          <div className="actions"><button className="btn solid" disabled={f.username.trim().length < 3 || f.password.length < 12}>Add admin</button></div>
        </form>
      )}
      {(msg.err || msg.ok) && <div className="panel-b">{msg.err && <p className="warn">{msg.err}</p>}{msg.ok && <p className="ok">{msg.ok}</p>}</div>}
    </Panel>
  )
}
