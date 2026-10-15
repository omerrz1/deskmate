import { useState } from 'react'
import { api, usePoll } from '../../api'
import { useConfirm, useToast } from '../../Feedback'

const MODES = [
  ['off', 'Off', 'Employees can only read pages with the simple web reader.'],
  ['read', 'Read only', 'They can open pages, scroll and follow links in a real browser, but cannot type or press buttons.'],
  ['full', 'Full', 'They can also type, choose options, press buttons and sign in with the saved logins below.'],
]

/** The real web browser employees can use, and the logins they may use in it. */
export default function Browser() {
  const toast = useToast()
  const confirm = useConfirm()
  const [d, reload] = usePoll('/browser/settings', 0)
  const [team] = usePoll('/agents', 0)
  const [test, setTest] = useState(null)
  const [busy, setBusy] = useState(false)
  const [f, setF] = useState({ label: '', site: '', username: '', password: '', agent_id: '' })
  if (!d) return null
  const setMode = async (mode) => {
    if (mode === 'full' && !(await confirm({ title: 'Allow full browsing?', body: 'Employees will be able to fill in forms and press buttons on public websites, and sign in with the saved logins. They cannot enter card numbers or passwords themselves, and cannot reach private networks.', confirm: 'Allow full browsing' }))) return
    try { await api.put('/browser/settings', { mode }); reload() } catch (e) { toast.err(e.message) }
  }
  const runTest = async () => { setBusy(true); setTest(null); try { setTest(await api.post('/browser/test')) } catch (e) { setTest({ ok: false, error: e.message }) } finally { setBusy(false) } }
  const add = async (e) => {
    e.preventDefault()
    try { await api.post('/browser/logins', { ...f, agent_id: f.agent_id ? +f.agent_id : null }); setF({ label: '', site: '', username: '', password: '', agent_id: '' }); reload(); toast.ok('Login saved') } catch (x) { toast.err(x.message) }
  }
  const remove = async (l) => { if (await confirm({ title: `Delete “${l.label}”?`, confirm: 'Delete', danger: true })) { try { await api.del(`/browser/logins/${l.id}`); reload() } catch (e) { toast.err(e.message) } } }
  return (
    <>
      <section className="panel"><div className="panel-b form-grid">
        <h3>What employees may do in the browser</h3>
        <div className="mode-cards" role="radiogroup" aria-label="Browser mode">
          {MODES.map(([k, n, why]) => (
            <button key={k} role="radio" aria-checked={d.mode === k} className={`mode-card ${d.mode === k ? 'on' : ''}`} onClick={() => setMode(k)}><b>{n}</b><span>{why}</span></button>
          ))}
        </div>
        <div className="actions"><button className="btn" disabled={busy} onClick={runTest}>{busy ? 'Opening a page…' : 'Test the browser'}</button>
          {test && (test.ok ? <span className="ok small">Works: opened “{test.title}” in {test.ms} ms.</span> : <span className="warn small">{test.error}</span>)}</div>
        <p className="faint small">Each employee gets their own private browser window. Pages on this machine or private networks are always blocked, and downloads are off. You can watch it live on their Computer.</p>
      </div></section>
      <section className="panel"><div className="panel-b">
        <h3>Saved logins</h3>
        <p className="muted small" style={{ marginBottom: 10 }}>Passwords are stored encrypted. Employees choose a login by name and the server types it in, only on that website. They never see the password.</p>
        {d.logins.length === 0 && <p className="faint">None yet.</p>}
        <ul className="plain">{d.logins.map((l) => (
          <li key={l.id} className="row-split"><span><b>{l.label}</b> <span className="muted small">· {l.site} · {l.agent_id ? (team || []).find((a) => a.id === l.agent_id)?.name || 'one employee' : 'any employee'}</span></span><button className="link danger" onClick={() => remove(l)}>Delete</button></li>
        ))}</ul>
        <form className="form-grid" onSubmit={add} style={{ marginTop: 14 }}>
          <div className="row2"><label className="f">Name<input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} placeholder="e.g. Shopify admin" /></label><label className="f">Website<input value={f.site} onChange={(e) => setF({ ...f, site: e.target.value })} placeholder="e.g. mystore.myshopify.com" /></label></div>
          <div className="row2"><label className="f">Username or email<input autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></label><label className="f">Password<input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label></div>
          <label className="f">Who may use it<select value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })}><option value="">Any employee</option>{(team || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <div className="actions"><button className="btn solid" disabled={!f.label || !f.site || !f.username || !f.password}>Save login</button></div>
        </form>
      </div></section>
    </>
  )
}
