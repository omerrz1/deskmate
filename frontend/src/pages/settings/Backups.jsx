import { useState } from 'react'
import { api, usePoll } from '../../api'
import { useConfirm, usePrompt, useToast } from '../../Feedback'

const size = (b) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`)

/** Daily backups of the whole workspace, a button to make one now, and a restore that asks for your password. */
export default function Backups() {
  const toast = useToast()
  const confirm = useConfirm()
  const prompt = usePrompt()
  const [d, reload] = usePoll('/backups', 0)
  const [busy, setBusy] = useState(false)
  if (!d) return null
  const now = async () => { setBusy(true); try { await api.post('/backups'); reload(); toast.ok('Backup saved') } catch (e) { toast.err(e.message) } finally { setBusy(false) } }
  const set = async (p) => { try { await api.put('/backups/settings', p); reload() } catch (e) { toast.err(e.message) } }
  const restore = async (b) => {
    if (!(await confirm({ title: `Restore ${b.name}?`, body: 'Everything the company has now is replaced by this backup (a safety copy of the current state is saved first). People signed in stay signed in.', confirm: 'Continue', danger: true }))) return
    const pw = await prompt({ title: 'Confirm with your password', label: 'Your password', secret: true, confirm: 'Restore' })
    if (!pw) return
    try { await api.post(`/backups/${encodeURIComponent(b.name)}/restore`, { password: pw }); toast.ok('Restored. Reloading…'); setTimeout(() => window.location.reload(), 900) } catch (e) { toast.err(e.message) }
  }
  const remove = async (b) => { if (await confirm({ title: 'Delete this backup?', confirm: 'Delete', danger: true })) { try { await api.del(`/backups/${encodeURIComponent(b.name)}`); reload() } catch (e) { toast.err(e.message) } } }
  return (
    <>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Automatic backups</h3>
        <div className="row2">
          <label className="f">Schedule<select value={d.settings.schedule} onChange={(e) => set({ schedule: e.target.value })}><option value="daily">Every night (about 03:00)</option><option value="off">Off</option></select></label>
          <label className="f">Keep the last<select value={d.settings.keep} onChange={(e) => set({ keep: +e.target.value })}>{[7, 14, 30, 60, 90].map((n) => <option key={n} value={n}>{n} days</option>)}</select></label>
        </div>
        <div className="actions"><button className="btn solid" disabled={busy} onClick={now}>{busy ? 'Saving…' : 'Back up now'}</button>
          <span className="faint small">{d.settings.last ? `Last scheduled backup: ${new Date(d.settings.last * 1000).toLocaleString()}` : 'No scheduled backup has run yet.'}</span></div>
        <p className="faint small">A backup holds your team, files, mail and settings. API keys inside it only work on this server.</p>
      </div></section>
      <section className="panel"><div className="panel-b">
        <h3>Backups</h3>
        {d.items.length === 0 && <p className="faint">None yet.</p>}
        <ul className="plain">{d.items.map((b) => (
          <li key={b.name} className="row-split"><span><b>{new Date(b.created_at * 1000).toLocaleString()}</b> <span className="muted small">· {b.kind} · {size(b.size)}</span></span>
            <span className="actions"><a className="btn small" href={`/api/backups/${encodeURIComponent(b.name)}/download`}>Download</a><button className="btn small" onClick={() => restore(b)}>Restore</button><button className="link danger" onClick={() => remove(b)}>Delete</button></span></li>
        ))}</ul>
      </div></section>
    </>
  )
}
