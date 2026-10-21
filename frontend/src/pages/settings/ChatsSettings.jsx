import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, usePoll } from '../../api'
import { useConfirm, useToast } from '../../Feedback'

const local = (o) => /^(https?:\/\/)?(localhost|127\.|\[::1\])/.test(o)

function Copy({ text, label = 'Copy' }) {
  const [done, setDone] = useState(false)
  return <button type="button" className="btn small" onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500) } catch { /* the field can still be selected by hand */ } }}>{done ? 'Copied' : label}</button>
}

function Field({ label, value, mono = true }) {
  return (
    <div className="ch-field"><span className="faint small">{label}</span>
      <div><input readOnly className={mono ? 'mono' : ''} value={value} onFocus={(e) => e.target.select()} aria-label={label} /><Copy text={value} /></div></div>
  )
}

function Status({ on, app }) {
  return on ? <span className="ch-chip ok">Connected</span> : <Link className="ch-chip warn" to={`/apps?connect=${app}`}>Not connected: connect it in Apps</Link>
}

/** Wires customers' messages and WhatsApp calls into Deskmates, and sets how employees answer. */
export default function ChatsSettings() {
  const toast = useToast()
  const confirm = useConfirm()
  const [s, reload] = usePoll('/chats-settings', 0)
  const [team] = usePoll('/agents', 0)
  const [scope] = usePoll('/apps-scope', 0)
  const [calling, setCalling] = useState(null)
  const [busy, setBusy] = useState(false)
  const [tgBase, setTgBase] = useState('')
  if (!s) return null
  const base = (s.public_base_url || window.location.origin).replace(/\/$/, '')
  const url = (k) => base + s.webhooks[k]
  const save = async (patch, msg = 'Saved') => { try { await api.put('/chats-settings', patch); reload(); toast.ok(msg) } catch (e) { toast.err(e.message) } }
  const checkCalling = async () => { setBusy(true); try { setCalling(await api.get('/chats-settings/calling')) } catch (e) { setCalling({ error: e.message }) } finally { setBusy(false) } }
  const configureCalling = async () => {
    setBusy(true)
    try {
      const result = await api.post('/chats-settings/calling/enable')
      setCalling(result); reload()
      toast.ok(result.ready ? 'Twilio is configured. Make a test call to verify audio.' : 'Setup submitted. Review the checks below; Twilio may still be updating.')
    } catch (e) { setCalling({ error: e.message }) } finally { setBusy(false) }
  }
  const rotate = async () => { if (!(await confirm({ title: 'Change the secret address?', body: 'The addresses below stop working. Run Twilio setup again and update the addresses in Meta and Telegram, or no messages will arrive.', confirm: 'Change it', danger: true }))) return; try { await api.post('/chats-settings/rotate'); setCalling(null); reload(); toast.ok('New address made') } catch (e) { toast.err(e.message) } }
  const connectTelegram = async () => { setBusy(true); try { await api.post('/chats-settings/telegram', { base_url: tgBase || base }); toast.ok('Telegram will now send messages here') } catch (e) { toast.err(e.message) } finally { setBusy(false) } }
  const anyMeta = ['whatsapp_cloud', 'instagram_business', 'facebook_page'].some((p) => s.connected[p].connected)
  return (
    <>
      <section className="panel"><div className="panel-b form-grid">
        <h3>How your team answers</h3>
        <div className="row2">
          <label className="f">When a customer writes<select value={s.reply_mode} onChange={(e) => save({ reply_mode: e.target.value })}>
            <option value="agent">The employee in charge writes a reply</option><option value="off">Just collect messages (nobody replies automatically)</option></select></label>
          <label className="f">When an employee replies<select value={s.messaging_policy} onChange={(e) => save({ messaging_policy: e.target.value })}>
            <option value="auto">Send automatically</option><option value="approve">Wait for my OK</option></select></label>
        </div>
        <label className="f">Employee in charge of new chats<select value={s.default_agent || ''} onChange={(e) => save({ default_agent: Number(e.target.value) })}>{(team || []).map((a) => <option key={a.id} value={a.id}>{a.name}{a.role ? ` (${a.role})` : ''}</option>)}</select></label>
        <p className="faint small">Customers' messages are treated as untrusted text. In a customer chat an employee can look things up but cannot change anything in your connected apps, send email, or reach other customers' details. You can change who is in charge of any chat on the Chats page.</p>
      </div></section>

      <section className="panel"><div className="panel-b form-grid">
        <h3>Connect your accounts</h3>
        {local(base) && <p className="warn small" style={{ marginTop: 0 }}>These addresses start with {base}, which only this computer can reach. Meta, Twilio and Telegram need a public https address. While testing, use a tunnel (such as ngrok or Cloudflare Tunnel) and open Deskmates through it, or set PUBLIC_BASE_URL on the server.</p>}
        <div className="ch-block">
          <div className="ch-block-h"><b>WhatsApp, Instagram and Messenger</b><span className="faint small">one address for all three</span></div>
          <div className="ch-status">{[['whatsapp_cloud', 'WhatsApp'], ['instagram_business', 'Instagram'], ['facebook_page', 'Messenger']].map(([p, n]) => <span key={p}><b>{n}</b> <Status on={s.connected[p].connected} app={p} /></span>)}</div>
          <Field label="Callback URL" value={url('meta')} />
          <Field label="Verify token" value={s.verify_token} />
          <ol className="ch-steps small">
            <li>In <b>Meta for Developers</b>, open your app. For <b>WhatsApp</b>: WhatsApp → Configuration → Webhook. For <b>Instagram</b>: Instagram → API setup → Webhooks. For a <b>Page</b>: Webhooks → Page.</li>
            <li>Paste the Callback URL and the Verify token, then press <b>Verify and save</b>.</li>
            <li>Subscribe to <b>messages</b> (and <b>calls</b> for WhatsApp calling).</li>
            <li>Add the <b>Meta app secret</b> to each connector in Apps, so Deskmates only accepts messages Meta has signed.</li>
          </ol>
          {anyMeta && ['whatsapp_cloud', 'instagram_business', 'facebook_page'].filter((p) => s.connected[p].connected && !s.app_secret_set[p]).map((p) => <p key={p} className="warn small">{s.connected[p].name} has no app secret yet: anyone who learns the address could send fake messages. Add it in Apps.</p>)}
        </div>
        <div className="ch-block">
          <div className="ch-block-h"><b>Text messages and WhatsApp through Twilio</b></div>
          <div className="ch-status"><Status on={s.connected.twilio.connected} app="twilio" /></div>
          <Field label="Webhook URL (HTTP POST)" value={url('twilio')} />
          <p className="faint small">Twilio Console → Phone Numbers → your number → Messaging → “A message comes in”: paste this address. For WhatsApp senders, use the same address in the sender's “Incoming messages” setting. PUBLIC_BASE_URL must be set so the server can verify Twilio’s signature.</p>
        </div>
        <div className="ch-block">
          <div className="ch-block-h"><b>Telegram</b></div>
          <div className="ch-status"><Status on={s.connected.telegram.connected} app="telegram" /></div>
          {s.connected.telegram.connected && (
            <div className="actions"><input style={{ flex: 1, minWidth: 220 }} placeholder={`Public https address of this server (default ${base})`} value={tgBase} onChange={(e) => setTgBase(e.target.value)} aria-label="Public address of this server" />
              <button className="btn" disabled={busy} onClick={connectTelegram}>Connect Telegram</button></div>
          )}
          <p className="faint small">This tells Telegram to send your bot's messages to Deskmates.</p>
        </div>
        <div className="actions"><button className="btn small danger" onClick={rotate}>Change the secret addresses</button><span className="faint small">Do this if an address was shared by mistake.</span></div>
      </div></section>

      <section className="panel"><div className="panel-b form-grid">
        <h3>WhatsApp calls through Twilio</h3>
        <p className="muted small" style={{ marginTop: 0 }}>Your employees can ask a WhatsApp customer's permission to call, and then talk with them by voice. Customers can also call you, and the assistant answers. Calls are never recorded; a written summary is kept on the Calls page and in the chat.</p>
        <div className="ch-status"><span><b>This server</b> {s.calling.installed ? <span className="ch-chip ok">Ready for calls</span> : <span className="ch-chip warn">Needs the aiortc package (pip install aiortc)</span>}</span>
          <span><b>Twilio</b> <Status on={s.connected.twilio.connected} app="twilio" /></span></div>
        <ol className="plain small">
          <li><Link to="/apps?connect=twilio">Connect your Twilio account</Link> with your Account SID, Auth token and registered WhatsApp sender number.</li>
          <li>Check the connection, then configure incoming calls and messages below. We create the voice application and calling permission template for you.</li>
          <li>Ask a test customer to message you, approve calling permission and answer a call.</li>
        </ol>
        <div className="actions">
          <button className="btn" disabled={busy || !s.connected.twilio.connected} onClick={checkCalling}>{busy ? 'Working…' : 'Check connection'}</button>
          <button className="btn solid" disabled={busy || !scope?.can_manage || !calling?.can_configure} onClick={configureCalling}>Configure incoming calls and messages</button>
          <Link className="btn" to="/apps?connect=twilio">Edit connection</Link>
        </div>
        <p className="faint small">Setup routes this sender’s incoming calls, messages and permission replies to the company workspace. It replaces the sender’s current voice app and messaging webhook. Your sender must already be registered and eligible for WhatsApp calling in Twilio.</p>
        {calling && <div aria-live="polite">
          {calling.error ? <p className="warn small">{calling.error}</p> : <>
            <p className={calling.ready ? 'ok small' : 'small'}><b>{calling.ready ? 'Configuration verified — ready for a test call' : 'Connection checklist'}</b></p>
            <ul className="plain small">{calling.checks?.map(check => <li key={check.id}><span className={check.ok ? 'ok' : 'warn'}>{check.ok ? '✓' : '○'} {check.label}</span> — {check.message}</li>)}</ul>
            {!calling.company_workspace && <p className="warn small">Switch to the company workspace to configure the shared Twilio sender.</p>}
            <p className="faint small">{calling.note}</p>
          </>}
        </div>}
        <details><summary>Manual setup and webhook addresses</summary>
          <Field label="Twilio Voice webhook (HTTP POST)" value={url('twilio_voice')} />
          <Field label="Twilio messaging webhook (HTTP POST)" value={url('twilio')} />
          <p className="muted small">In Twilio Console, create a TwiML app with this Voice request URL and select it in your WhatsApp sender’s Voice Endpoint Configuration. Use the messaging webhook on the sender. Add a Voice Call Request Content SID in Apps → Twilio.</p>
        </details>
        <div className="row2">
          <label className="f">When an employee wants to call<select value={s.call_policy} onChange={(e) => save({ call_policy: e.target.value })}><option value="auto">Start after the customer allows it</option><option value="approve">Wait for admin approval too</option></select></label>
          <label className="f">When a customer calls you on WhatsApp<select value={s.inbound_calls} onChange={(e) => save({ inbound_calls: e.target.value })}><option value="off">Decline the call</option><option value="answer">The assistant answers</option></select></label>
        </div>
        <ul className="ch-rules small">
          <li>A customer must write to you first, and tap <b>Allow</b> on a permission request, before you can call them.</li>
          <li>WhatsApp limits permission requests (about one a day and two a week) and removes permission after several unanswered calls.</li>
          <li>Business-started calls depend on your sender country and Twilio eligibility, and provider charges apply.</li>
          <li>Calls stop after {s.calling.max_minutes} minutes. Customers who asked not to be called are never called.</li>
          <li>Set PUBLIC_BASE_URL to your public HTTPS address. Your server must accept secure WebSocket connections from Twilio.</li>
        </ul>
      </div></section>
    </>
  )
}
