import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, apiFetch, usePoll } from '../../api'
import { useConfirm } from '../../Feedback'

const VOICES = ['marin', 'cedar', 'coral', 'sage', 'ballad', 'verse', 'ash', 'alloy', 'echo', 'shimmer', 'nova', 'onyx', 'fable']
const COLORS = ['#fe5011', '#221d1b', '#8fb3c6', '#c45423', '#12806f', '#7c4a8b', '#d4a373', '#4f6d5a']

export default function AgentSettings({ a, reload }) {
  const nav = useNavigate()
  const confirm = useConfirm()
  const [f, setF] = useState(null)
  const [team] = usePoll('/agents', 0)
  const [msg, setMsg] = useState('')
  const [mail, setMail] = useState({ email: a.email || '', password: '' })
  const [test, setTest] = useState(null)
  const [busy, setBusy] = useState('')
  useEffect(() => {
    setF({ name: a.name, gender: a.gender, role: a.role, personality: a.personality, persona: a.persona, bio: a.bio, openai_voice: a.openai_voice, color: a.color, autonomy: a.autonomy, signature: a.signature || '', works_alone: !!a.works_alone, spend_cap_day: a.spend_cap_day || 0, approve_outside: !!a.approve_outside })
    setMail({ email: a.email || '', password: '' }); setTest(null)
  }, [a.id]) // eslint-disable-line
  if (!f) return null
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const guard = async (name, fn) => { setMsg(''); setBusy(name); try { await fn() } catch (e) { setMsg(e.message) } setBusy('') }

  const save = () => guard('save', async () => { await api.patch(`/agents/${a.id}`, f); setMsg('Saved.'); reload() })
  const preview = () => guard('voice', async () => {
    const r = await apiFetch(`/api/agents/${a.id}/speak`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: `Hi, I'm ${f.name}. This is how I sound in meetings.` }) })
    if (!r.ok) throw new Error('Could not play the voice')
    new Audio(URL.createObjectURL(await r.blob())).play()
  })
  const saveMail = () => guard('mail', async () => {
    const r = await api.put(`/agents/${a.id}/mail`, mail); setMail({ ...mail, password: '' }); setTest(r.test); reload()
    setMsg(r.test?.ok ? 'Email is working.' : 'Saved. See the test result below.')
  })
  const retest = () => guard('test', async () => { setTest(await api.post(`/agents/${a.id}/mail/test`, {})) })
  const toggleCap = (key, on) => guard('caps', async () => {
    const off = a.capabilities.filter((c) => !c.on && !c.locked && c.key !== key).map((c) => c.key)
    if (!on) off.push(key)
    await api.put(`/agents/${a.id}/capabilities`, { off }); reload()
  })
  const setManager = (on) => guard('mgr', async () => { await api.put(`/agents/${a.id}/manager`, { is_manager: on }); reload() })
  const setBoss = (id) => guard('boss', async () => { await api.put(`/agents/${a.id}/manager`, { manager_id: +id || 0 }); reload() })
  const duplicate = () => guard('dup', async () => { const n = await api.post(`/agents/${a.id}/duplicate`); nav(`/agents/${n.id}`) })
  const remove = () => guard('rm', async () => {
    if (!(await confirm({ title: `Remove ${a.name}?`, body: 'Their campaigns go back to the default persona and their open to-dos are closed.', confirm: 'Remove', danger: true }))) return
    await api.del(`/agents/${a.id}`); nav('/')
  })
  const others = (team || []).filter((x) => x.id !== a.id)

  const Sec = ({ title, children, open }) => <details className="section" open={open}><summary style={{ cursor: 'pointer', fontWeight: 600, marginBottom: 12 }}>{title}</summary><div className="form-grid">{children}</div></details>
  return (
    <div style={{ maxWidth: 620 }}>
      {msg && <p className="ok small" style={{ marginTop: 0 }}>{msg}</p>}
      <section className="section form-grid">
        <div className="row2">
          <label className="f">Name<input value={f.name} onChange={set('name')} /></label>
          <label className="f">Job title<input value={f.role} onChange={set('role')} /></label>
        </div>
        <label className="f">Personality<textarea rows="3" value={f.personality} onChange={set('personality')} /></label>
        <div className="row2">
          <label className="f">Voice for calls and meetings<select value={f.openai_voice} onChange={set('openai_voice')}>{VOICES.map((v) => <option key={v}>{v}</option>)}</select></label>
          <label className="f">Works<select value={f.autonomy} onChange={set('autonomy')}><option value="full">On their own, then reports back</option><option value="ask">Asks me before changing anything</option></select></label>
        </div>
        <label className="check"><input type="checkbox" checked={f.works_alone} onChange={(e) => setF({ ...f, works_alone: e.target.checked })} /> Works through their to-do list by themselves</label>
        <label className="check"><input type="checkbox" checked={f.approve_outside} onChange={(e) => setF({ ...f, approve_outside: e.target.checked })} /> Every email they send outside the company waits for my approval</label>
        <label className="f">Daily spend cap, in USD <span className="faint">(0 uses the company default)</span><input type="number" min="0" step="0.5" value={f.spend_cap_day} onChange={(e) => setF({ ...f, spend_cap_day: e.target.value })} style={{ maxWidth: 160 }} /></label>
        <details><summary className="muted" style={{ cursor: 'pointer' }}>More about them</summary>
          <div className="form-grid" style={{ marginTop: 10 }}>
            <label className="f">Gender<select value={f.gender} onChange={set('gender')}><option value="female">Female</option><option value="male">Male</option><option value="non-binary">Non-binary</option></select></label>
            <label className="f">How they behave on phone calls<textarea rows="4" value={f.persona} onChange={set('persona')} placeholder="They write this themselves, or write it here." /></label>
            <label className="f">Short bio<textarea rows="2" value={f.bio} onChange={set('bio')} /></label>
            <label className="f">Email signature<input value={f.signature} onChange={set('signature')} placeholder={`${a.name}, ${a.role}`} /></label>
            <div className="f">Colour<div className="swatches">{COLORS.map((c) => <button key={c} className={`swatch ${f.color === c ? 'on' : ''}`} style={{ background: c }} onClick={() => setF({ ...f, color: c })} aria-label={c} />)}</div></div>
          </div>
        </details>
        <div className="actions"><button className="btn solid" disabled={!!busy} onClick={save}>Save</button><button className="btn" onClick={preview}>Hear their voice</button></div>
      </section>

      <Sec title="Manager">
        <label className="check"><input type="checkbox" checked={!!a.is_manager} onChange={(e) => setManager(e.target.checked)} /> {a.name} is a manager (runs the others and can do what you can)</label>
        {!!a.is_manager && a.reports.length > 0 && <p className="small muted">Manages: {a.reports.map((r) => r.name).join(', ')}</p>}
        {!a.is_manager && <label className="f">Reports to<select value={a.manager?.id || ''} onChange={(e) => setBoss(e.target.value)}><option value="">Nobody</option>{others.filter((x) => x.is_manager).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
      </Sec>

      <Sec title="Abilities">
        <ul className="capsw" style={{ margin: 0 }}>
          {a.capabilities.map((c) => (
            <li key={c.key}><label className="check"><input type="checkbox" checked={c.on} disabled={c.locked || busy === 'caps'} onChange={(e) => toggleCap(c.key, e.target.checked)} /> <span><b>{c.label}</b><small>{c.description}</small></span></label></li>
          ))}
        </ul>
        <p className="small muted">Phone calling for the whole team is under <Link className="link" to="/settings/calling">Settings → Calling</Link>.</p>
      </Sec>

      <Sec title="Work email">
        <label className="f">Email address<input type="email" value={mail.email} onChange={(e) => setMail({ ...mail, email: e.target.value })} placeholder="name@gmail.com" /></label>
        <label className="f">App password {a.has_mail_password && <span className="ok small">(saved)</span>}<input type="password" autoComplete="new-password" value={mail.password} onChange={(e) => setMail({ ...mail, password: e.target.value })} placeholder={a.has_mail_password ? 'Leave empty to keep the saved one' : 'The 16-letter code from Google'} /></label>
        <div className="actions">
          <button className="btn solid" disabled={!mail.email || !!busy} onClick={saveMail}>{busy === 'mail' ? 'Checking…' : 'Save and test'}</button>
          {a.has_mail_password && <button className="btn" disabled={!!busy} onClick={retest}>Test again</button>}
          {a.has_mail_password && <button className="link" onClick={() => api.del(`/agents/${a.id}/mail/password`).then(reload)}>Remove password</button>}
        </div>
        {test && (
          <ul className="testlist">
            <li className={test.smtp === 'ok' ? 'good' : 'bad'}><b>Sending</b> {test.smtp === 'ok' ? 'works' : test.smtp}</li>
            <li className={test.imap === 'ok' ? 'good' : 'bad'}><b>Receiving</b> {test.imap === 'ok' ? 'works' : test.imap}</li>
          </ul>
        )}
        <details className="guide">
          <summary>How to get a Gmail app password</summary>
          <ol>
            <li>Turn on 2-Step Verification at <a href="https://myaccount.google.com/security" target="_blank" rel="noreferrer">myaccount.google.com/security</a>.</li>
            <li>Open <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">myaccount.google.com/apppasswords</a>, name it Deskmates and click Create.</li>
            <li>Copy the 16-letter password (Google shows it once) and paste it above with the Gmail address.</li>
          </ol>
          <p className="small muted">"Sign-in refused" usually means a normal password was used or 2-Step Verification is off. If sending works but receiving fails, enable IMAP in Gmail settings. Company mail servers go under <Link to="/settings/email">Settings → Email</Link>.</p>
        </details>
      </Sec>

      <section className="section">
        <div className="actions"><button className="btn" onClick={duplicate}>Duplicate {a.name}</button><button className="btn danger" onClick={remove}>Remove {a.name}</button></div>
      </section>
    </div>
  )
}
