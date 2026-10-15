import { useEffect, useState } from 'react'
import { api, usePoll } from '../../api'
import { useToast } from '../../Feedback'

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

/** Where alerts and the daily or weekly summary go: email, Slack/Discord, Telegram. */
export default function Notifications() {
  const toast = useToast()
  const [c, reload] = usePoll('/notifications', 0)
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (c) setF({ email: c.email, digest: c.digest, digest_hour: c.digest_hour, digest_day: c.digest_day, alerts: c.alerts, telegram_chat: c.telegram_chat, slack: '', telegram_token: '' }) }, [c])
  if (!c || !f) return null
  const save = async () => {
    setBusy(true)
    const body = { ...f }
    if (!body.slack) delete body.slack
    if (!body.telegram_token) delete body.telegram_token
    try { await api.put('/notifications', body); reload(); toast.ok('Saved') } catch (e) { toast.err(e.message) } finally { setBusy(false) }
  }
  const out = (r) => Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(' · ')
  const test = async () => { try { toast.info(out(await api.post('/notifications/test')) || 'No channel is switched on') } catch (e) { toast.err(e.message) } }
  const digest = async () => { try { toast.info('Sent. ' + out(await api.post('/notifications/digest-now'))) } catch (e) { toast.err(e.message) } }
  return (
    <>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Where to send</h3>
        <label className="check"><input type="checkbox" checked={f.email} onChange={(e) => setF({ ...f, email: e.target.checked })} /> Email{c.admin_email ? ` to ${c.admin_email}` : ' (set your address in Settings → Email, otherwise it lands in your Deskmates inbox)'}</label>
        <label className="f">Slack, Discord or Teams webhook {c.slack_set && <span className="muted">(saved; paste a new one to replace)</span>}<input type="password" autoComplete="off" placeholder="https://hooks.slack.com/services/…" value={f.slack} onChange={(e) => setF({ ...f, slack: e.target.value })} /></label>
        <div className="row2">
          <label className="f">Telegram bot token {c.telegram_set && <span className="muted">(saved)</span>}<input type="password" autoComplete="off" placeholder="from @BotFather" value={f.telegram_token} onChange={(e) => setF({ ...f, telegram_token: e.target.value })} /></label>
          <label className="f">Telegram chat ID<input value={f.telegram_chat} onChange={(e) => setF({ ...f, telegram_chat: e.target.value })} placeholder="e.g. 123456789" /></label>
        </div>
      </div></section>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Summary</h3>
        <div className="row2">
          <label className="f">How often<select value={f.digest} onChange={(e) => setF({ ...f, digest: e.target.value })}><option value="daily">Every day</option><option value="weekly">Every week</option><option value="off">Never</option></select></label>
          <label className="f">At<select value={f.digest_hour} onChange={(e) => setF({ ...f, digest_hour: +e.target.value })}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}</select></label>
        </div>
        {f.digest === 'weekly' && <label className="f">On<select value={f.digest_day} onChange={(e) => setF({ ...f, digest_day: +e.target.value })}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></label>}
      </div></section>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Tell me straight away when</h3>
        {Object.entries(c.kinds).map(([k, label]) => (
          <label key={k} className="check"><input type="checkbox" checked={f.alerts.includes(k)} onChange={(e) => setF({ ...f, alerts: e.target.checked ? [...f.alerts, k] : f.alerts.filter((x) => x !== k) })} /> {label}</label>
        ))}
      </div></section>
      <div className="actions"><button className="btn solid" disabled={busy} onClick={save}>Save</button><button className="btn" onClick={test}>Send a test</button><button className="btn" onClick={digest}>Send the summary now</button></div>
    </>
  )
}
