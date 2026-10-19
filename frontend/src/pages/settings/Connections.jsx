import { useState } from 'react'
import { api, usePoll } from '../../api'
import { useConfirm, useToast } from '../../Feedback'

/** The company's own OpenAI and ElevenLabs accounts: check them, replace the key, or disconnect ElevenLabs. */
export default function Connections() {
  const toast = useToast()
  const confirm = useConfirm()
  const [s, reload] = usePoll('/setup', 0)
  const [edit, setEdit] = useState(null) // 'openai' | 'eleven'
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState({})
  if (!s) return null
  const save = async (name) => {
    setBusy(true)
    try { await api.post(`/setup/${name}`, { key }); setEdit(null); setKey(''); reload(); toast.ok('Connected') } catch (e) { toast.err(e.message) } finally { setBusy(false) }
  }
  const test = async () => { try { setRes(await api.post('/setup/test')) } catch (e) { toast.err(e.message) } }
  const disconnect = async () => {
    if (!(await confirm({ title: 'Disconnect ElevenLabs?', body: 'This disconnects ElevenLabs across every workspace in your company. Phone calls will use the OpenAI voice until it is reconnected.', confirm: 'Disconnect', danger: true }))) return
    try { await api.del('/setup/eleven'); reload() } catch (e) { toast.err(e.message) }
  }
  const card = (name, title, why, st) => (
    <section className="panel conn" key={name}><div className="panel-b">
      <div className="row-split"><div><h3>{title}</h3><p className="muted small" style={{ maxWidth: 520 }}>{why}</p></div>
        <span className={`pj-chip ${st.connected ? 'done' : ''}`}>{st.connected ? `Connected · …${st.last4}` : st.skipped ? 'Skipped' : 'Not connected'}</span></div>
      {res[name] && <p className={res[name].ok ? 'ok small' : 'warn small'}>{res[name].message}</p>}
      {edit === name ? (
        <form className="actions" style={{ marginTop: 12 }} onSubmit={(e) => { e.preventDefault(); save(name) }}>
          <input type="password" autoComplete="off" autoFocus style={{ flex: 1, minWidth: 260 }} placeholder="Paste the new key" value={key} onChange={(e) => setKey(e.target.value)} />
          <button className="btn solid" disabled={busy || key.trim().length < 20}>{busy ? 'Checking…' : 'Save'}</button><button type="button" className="btn" onClick={() => { setEdit(null); setKey('') }}>Cancel</button>
        </form>
      ) : (
        <div className="actions" style={{ marginTop: 12 }}>
          <button className="btn" disabled={s.can_manage === false} onClick={() => { setEdit(name); setKey('') }}>{st.connected ? 'Replace key' : 'Connect'}</button>
          {name === 'eleven' && st.connected && <button className="btn danger" disabled={s.can_manage === false} onClick={disconnect}>Disconnect</button>}
        </div>
      )}
    </div></section>
  )
  return (
    <>
      <p className="muted" style={{ marginBottom: 14, maxWidth: 640 }}>These company accounts are shared across all workspaces, so usage is billed to the company’s connected accounts. Keys are checked with the provider, stored encrypted on this server, and never shown again.</p>
      {s.can_manage === false && <p className="notice-soft">Your company admin manages these connections. They are already available to your workspace.</p>}
      {card('openai', 'OpenAI', 'Thinking, writing, planning, meetings and summaries. Required.', s.openai)}
      {card('eleven', 'ElevenLabs', 'Natural voices on live phone calls. Used for calls only.', s.eleven)}
      <div className="actions"><button className="btn" disabled={s.can_manage === false} onClick={test}>Test both connections</button></div>
    </>
  )
}
