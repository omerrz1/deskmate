import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, usePoll, ago, clock } from '../api'
import { onChange } from '../live'
import Avatar from '../Avatar'
import Icon from '../Icon'
import { EmptyState, PageHeader, Skeleton, useTitle } from '../ui'
import { useConfirm, useToast } from '../Feedback'

const CH = { whatsapp: ['WhatsApp', '#1f9d55', 'W'], instagram: ['Instagram', '#c13584', 'I'], messenger: ['Messenger', '#0a7cff', 'M'], sms: ['Text message', '#5b6770', 'T'], telegram: ['Telegram', '#229ed9', 'T'] }
const FILTERS = [['open', 'Open'], ['unread', 'Unread'], ['waiting', 'Waiting for OK'], ['closed', 'Closed']]
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

export function ChannelDot({ channel, size = 30 }) {
  const [name, color, letter] = CH[channel] || ['Chat', '#666', '?']
  return <span className="ch-dot" style={{ width: size, height: size, background: color, fontSize: size * 0.45 }} title={name} aria-label={name}>{letter}</span>
}

/** A loaded copy of one chat that refreshes when its tables change. */
function useChat(id) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(async () => {
    if (!id) { setD(null); return }
    try { setD(await api.get(`/chats/${id}`)); setErr('') } catch (e) { setErr(e.message) }
  }, [id])
  useEffect(() => {
    setD(null)
    load()
    let deb
    const off = onChange((t) => { if (t.some((x) => ['chats', 'chat_messages', 'calls'].includes(x))) { clearTimeout(deb); deb = setTimeout(load, 150) } })
    const timer = setInterval(load, 8000)
    return () => { off(); clearInterval(timer); clearTimeout(deb) }
  }, [load])
  return [d, load, err]
}

export default function Chats() {
  useTitle('Chats')
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const open = Number(sp.get('open')) || 0
  const [filter, setFilter] = useState('open')
  const [q, setQ] = useState('')
  const [data, reload] = usePoll(`/chats?status=${filter}&q=${encodeURIComponent(q)}`, 6000, [filter, q])
  const [team] = usePoll('/agents', 0)
  const [live] = usePoll('/chats/calls/live', 3000)
  const [settings] = usePoll('/chats-settings', 0)
  const [adding, setAdding] = useState(false)
  const list = data?.chats || []
  const setOpen = (id) => setSp(id ? { open: String(id) } : {}, { replace: true })
  const none = settings && !Object.values(settings.connected || {}).some((c) => c.connected)
  return (
    <div className="page wide chats-page">
      <PageHeader title="Chats" sub="Customers who write to you on WhatsApp, Instagram, Messenger, text or Telegram, and the replies your team sends."
        actions={<><Link className="btn big" to="/settings/chats">Set up</Link>{(settings?.connected?.twilio?.connected || settings?.connected?.whatsapp_cloud?.connected) && <button className="btn solid big" onClick={() => setAdding(true)}><Icon name="plus" size={15} /> New WhatsApp chat</button>}</>} />
      {none && (
        <EmptyState title="Connect a messaging app to get started" hint="Connect WhatsApp Business, Instagram, a Facebook Page, Twilio or Telegram, then point it at Deskmates in Settings → Chats. Your employees answer, and you stay in charge of what is sent."
          action={<><Link className="btn solid" to="/apps">Connect an app</Link> <Link className="btn" to="/settings/chats">How it works</Link></>} />
      )}
      {!none && (
        <div className="ch-wrap">
          <div className={`ch ${open ? 'has-open' : ''}`}>
            <section className="ch-list" aria-label="Conversations">
              <div className="ch-tools">
                <div className="mx-search"><Icon name="search" size={15} /><input placeholder="Search chats" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search chats" /></div>
              </div>
              <div className="ch-tabs" role="tablist">
                {FILTERS.map(([k, n]) => <button key={k} role="tab" aria-selected={filter === k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>{n}{k === 'unread' && data?.counts?.unread > 0 && <i>{data.counts.unread}</i>}{k === 'waiting' && data?.counts?.approvals > 0 && <i>{data.counts.approvals}</i>}</button>)}
              </div>
              <div className="ch-rows">
                {!data && <Skeleton rows={5} h={64} />}
                {data && list.length === 0 && <div className="mx-empty"><Icon name="inbox" size={26} /><b>No chats here</b><span>{filter === 'open' ? 'When a customer writes, it shows up here.' : 'Nothing matches.'}</span></div>}
                {list.map((c) => (
                  <button key={c.id} className={`ch-row ${open === c.id ? 'on' : ''} ${c.unread ? 'unread' : ''}`} onClick={() => setOpen(c.id)} aria-current={open === c.id ? 'true' : undefined}>
                    <ChannelDot channel={c.channel} />
                    <span className="ch-main">
                      <span className="ch-top"><b>{c.title}</b><time>{ago(c.last_at)}</time></span>
                      <span className="ch-pre">{c.last_preview || 'No messages yet'}</span>
                      <span className="ch-meta">{c.waiting > 0 && <em className="ch-chip warn">Needs your OK</em>}{c.channel === 'whatsapp' && c.call_perm === 'granted' && <em className="ch-chip">Can be called</em>}{c.agent_name && <small>{c.agent_name}</small>}</span>
                    </span>
                    {c.unread > 0 && <i className="ch-badge">{c.unread}</i>}
                  </button>
                ))}
              </div>
            </section>
            <section className="ch-pane" aria-label="Conversation">
              {open ? <Thread id={open} team={team || []} live={live || []} onChanged={reload} onBack={() => setOpen(0)} /> : (
                <div className="mx-none"><Icon name="mail" size={30} /><b>Select a conversation</b><span>Pick a chat on the left to read it and reply.</span></div>
              )}
            </section>
          </div>
        </div>
      )}
      {adding && <NewChat onClose={() => setAdding(false)} onDone={(id) => { setAdding(false); reload(); setOpen(id); toast.ok('Message sent') }} />}
    </div>
  )
}

function Thread({ id, team, live, onChanged, onBack }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [d, load, err] = useChat(id)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [tpl, setTpl] = useState({ name: '', language: 'en_US', params: '' })
  const [calling, setCalling] = useState(null)  // { objective } while the admin writes the goal
  const end = useRef(null)
  const unread = d?.unread || 0
  useEffect(() => { if (d && unread > 0) api.post(`/chats/${id}/read`).then(onChanged).catch(() => {}) }, [id, unread]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [d?.messages?.length, id])
  useEffect(() => { setText(''); setCalling(null) }, [id])
  if (err && !d) return <div className="mx-none"><b>This chat could not be opened</b><span>{err}</span></div>
  if (!d) return <div className="ch-load"><Skeleton rows={4} h={56} /></div>
  const act = async (fn, okMsg) => { setBusy(true); try { await fn(); if (okMsg) toast.ok(okMsg); await load(); onChanged() } catch (e) { toast.err(e.message) } finally { setBusy(false) } }
  const send = () => act(async () => { await api.post(`/chats/${id}/send`, { text }); setText('') })
  const sendTemplate = () => act(async () => { await api.post(`/chats/${id}/send`, { template: { name: tpl.name.trim(), language: tpl.language.trim() || 'en_US', params: tpl.params.split(',').map((x) => x.trim()).filter(Boolean) } }); setTpl({ ...tpl, name: '', params: '' }) }, 'Template sent')
  const call = d.call || {}
  const myCall = live.find((c) => c.chat_id === id)
  const closedWa = !d.window_open && d.channel === 'whatsapp'
  const winText = d.window_open ? (d.window_left != null ? `You can reply freely for ${d.window_left >= 3600 ? Math.floor(d.window_left / 3600) + 'h' : Math.max(1, Math.floor(d.window_left / 60)) + ' min'}` : 'No time limit on replies') : (d.channel === 'whatsapp' ? '24 hours have passed: only an approved template can be sent' : '24-hour reply window has closed')
  const agent = team.find((a) => a.id === d.agent_id)
  return (
    <div className="ch-thread">
      <header className="ch-head">
        <button className="ch-back btn small ghost" onClick={onBack} aria-label="Back to the list"><Icon name="back" size={15} /></button>
        <ChannelDot channel={d.channel} size={36} />
        <div className="ch-who"><b>{d.title}</b><small>{d.channel_label}{d.ext_id ? ` · +${d.ext_id}` : ''}{d.handle ? ` · ${d.handle}` : ''}{d.contact_id ? <> · <Link to={`/clients?open=${d.contact_id}`}>customer record</Link></> : null}</small></div>
        <div className="ch-acts">
          <label className="ch-assign"><span className="faint small">Handled by</span>
            <select value={d.agent_id || ''} onChange={(e) => act(() => api.post(`/chats/${id}/assign`, { agent_id: Number(e.target.value) }))} aria-label="Employee in charge">{team.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <button className="btn small" disabled={busy} onClick={() => act(() => api.post(`/chats/${id}/ai-reply`), `${agent?.name || 'The employee'} is writing a reply`)} title="Ask the employee to answer the latest message now"><Icon name="bolt" size={14} /> Let {agent?.name || 'them'} answer</button>
          <button className="btn small" disabled={busy} onClick={() => act(() => api.post(`/chats/${id}/auto`, { off: !d.auto_off }))} title={d.auto_off ? 'Automatic replies are off for this chat' : 'Automatic replies are on for this chat'}>{d.auto_off ? 'Auto-replies off' : 'Auto-replies on'}</button>
          <button className="btn small" disabled={busy} onClick={() => act(() => api.post(`/chats/${id}/status`, { status: d.status === 'open' ? 'closed' : 'open' }))}>{d.status === 'open' ? 'Close' : 'Reopen'}</button>
        </div>
      </header>

      <div className="ch-strip">
        <span className={`ch-chip ${d.window_open ? 'ok' : 'warn'}`}><Icon name={d.window_open ? 'check' : 'alert'} size={12} /> {winText}</span>
        {call.possible && (
          call.permission === 'granted'
            ? <span className="ch-chip ok"><Icon name="phone" size={12} /> Allowed calls{call.permission_until ? ` until ${clock(call.permission_until)}` : ''}</span>
            : <span className="ch-chip">{d.call_perm === 'denied' ? 'Said no to calls' : 'Has not allowed calls yet'}</span>
        )}
        <span className="ch-strip-sp" />
        {call.possible && !myCall && (call.permission === 'granted'
          ? <button className="btn small solid" disabled={busy} onClick={() => setCalling({ objective: '' })}><Icon name="phone" size={14} /> Call on WhatsApp</button>
          : <button className="btn small" disabled={busy || !d.window_open || d.call_perm === 'denied'} title={!d.window_open ? 'The 24-hour window has closed' : ''} onClick={() => act(() => api.post(`/chats/${id}/call-permission`, { text: '' }), 'Asked for permission to call')}><Icon name="phone" size={14} /> Ask permission to call</button>)}
        {d.channel === 'whatsapp' && !call.installed && <small className="faint">Calls need the aiortc package on the server</small>}
      </div>

      {calling && (
        <div className="ch-call-form">
          <label>What is the call for?<input autoFocus value={calling.objective} onChange={(e) => setCalling({ objective: e.target.value })} placeholder="e.g. Talk through the boiler service quote and book a visit" /></label>
          <div className="actions"><button className="btn solid" disabled={busy} onClick={() => act(async () => { await api.post(`/chats/${id}/call`, { objective: calling.objective }); setCalling(null) }, 'Calling…')}>Start call</button><button className="btn" onClick={() => setCalling(null)}>Cancel</button></div>
          <p className="faint small">{agent?.name || 'The employee'} talks with them by voice. Calls are never recorded; a written summary is kept.</p>
        </div>
      )}
      {myCall && (
        <div className="ch-live" role="status">
          <div className="ch-live-top"><span className="dot live" /><b>{myCall.answered ? `On a WhatsApp call · ${mmss(myCall.seconds)}` : 'Calling…'}</b><span className="ch-strip-sp" />
            <button className="btn small danger" onClick={async () => { if (await confirm({ title: 'End the call?', body: 'The customer is hung up on.', confirm: 'End call', danger: true })) act(() => api.post(`/chats/calls/${myCall.call_id}/end`)) }}>End call</button></div>
          {myCall.lines.length > 0 && <ul className="ch-lines">{myCall.lines.map((l, i) => <li key={i} className={l.role}><b>{l.role === 'agent' ? (agent?.name || 'Assistant') : d.title}</b> {l.text}</li>)}</ul>}
        </div>
      )}

      <div className="ch-msgs" role="log" aria-live="polite">
        {d.messages.length === 0 && <p className="muted small" style={{ padding: 20 }}>No messages yet.</p>}
        {d.messages.map((m) => <Message key={m.id} m={m} title={d.title} onAct={act} />)}
        <div ref={end} />
      </div>

      {closedWa ? (
        <div className="ch-tpl">
          <p className="small"><b>WhatsApp only lets you start again with an approved template.</b> For Twilio, enter the approved template’s Content SID (HX…). For Meta, enter its template name. The customer can reply freely for 24 hours once they answer.</p>
          <div className="ch-tpl-row">
            <label>Template Content SID or name<input value={tpl.name} onChange={(e) => setTpl({ ...tpl, name: e.target.value })} placeholder="e.g. appointment_reminder" /></label>
            <label>Language<input value={tpl.language} onChange={(e) => setTpl({ ...tpl, language: e.target.value })} style={{ width: 100 }} /></label>
            <label>Values for {'{{1}}'}, {'{{2}}'}… <span className="faint">(comma separated)</span><input value={tpl.params} onChange={(e) => setTpl({ ...tpl, params: e.target.value })} placeholder="Priya, Tuesday 10am" /></label>
            <button className="btn solid" disabled={busy || !tpl.name.trim()} onClick={sendTemplate}>Send template</button>
          </div>
        </div>
      ) : (
        <form className="ch-compose" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send() }}>
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) { e.preventDefault(); send() } }}
            placeholder={d.status === 'closed' ? 'This chat is closed. Writing reopens it.' : `Write to ${d.title}…`} aria-label="Your message" />
          <div className="ch-compose-act"><span className="faint small">Sends straight away as you. ⌘↵ to send.</span><button className="btn solid" disabled={busy || !text.trim()}>Send</button></div>
        </form>
      )}
    </div>
  )
}

function Message({ m, title, onAct }) {
  const out = m.direction === 'out'
  const file = m.media?.has_file ? `/api/chats/messages/${m.id}/file` : ''
  if (m.kind === 'call' || m.kind === 'call_permission_request' || m.kind === 'call_permission_reply' || m.kind === 'call_request') {
    const pending = m.status === 'pending_approval'
    return (
      <div className={`ch-sys ${pending ? 'pending' : ''}`}>
        <Icon name="phone" size={14} />
        <div>
          <span>{m.kind === 'call_request' ? `Wants to call them: ${m.body}` : m.kind === 'call_permission_request' ? `Asked permission to call: “${m.body}”` : m.body}</span>
          <small>{m.by ? `${m.by} · ` : ''}{clock(m.created_at)}{m.status === 'rejected' ? ' · declined' : ''}</small>
          {pending && <div className="ch-ok"><button className="btn small solid" onClick={() => onAct(() => api.post(`/chats/messages/${m.id}/approve`), m.kind === 'call_request' ? 'Calling…' : 'Sent')}>{m.kind === 'call_request' ? 'Approve call' : 'Approve'}</button><button className="btn small" onClick={() => onAct(() => api.post(`/chats/messages/${m.id}/reject`))}>Decline</button></div>}
        </div>
      </div>
    )
  }
  if (m.kind === 'reaction') return <div className={`ch-sys small`}><span>{out ? 'You' : title} reacted {m.body}</span></div>
  const pending = m.status === 'pending_approval'
  const failed = m.status === 'failed'
  return (
    <div className={`ch-msg ${out ? 'out' : 'in'} ${pending ? 'pending' : ''} ${failed ? 'failed' : ''}`}>
      <div className="ch-bubble">
        {m.kind === 'image' && file && <a href={file} target="_blank" rel="noreferrer"><img src={file} alt="Photo they sent" loading="lazy" className="ch-img" /></a>}
        {(m.kind === 'voice' || m.kind === 'audio') && file && <audio controls preload="none" src={file} aria-label="Voice message" />}
        {(m.kind === 'document' || m.kind === 'video') && file && <a className="ch-file" href={file} target="_blank" rel="noreferrer"><Icon name="file" size={14} /> {m.media?.filename || (m.kind === 'video' ? 'Video' : 'Document')}</a>}
        {m.kind === 'template' && <em className="ch-tplmark">Message template</em>}
        {m.body && <p>{m.body}</p>}
        {!m.body && !file && <p className="faint">({m.kind})</p>}
      </div>
      <div className="ch-sub">
        {out && m.by && <span>{m.by}</span>}
        <time>{clock(m.created_at)}</time>
        {out && !pending && <span className={`ch-state ${failed ? 'bad' : ''}`}>{failed ? `Not sent: ${m.error || 'failed'}` : m.status === 'read' ? 'Read' : m.status === 'delivered' ? 'Delivered' : m.status === 'sent' ? 'Sent' : m.status === 'rejected' ? 'Declined' : ''}</span>}
        {!out && m.error && <span className="ch-state bad">{m.error}</span>}
      </div>
      {pending && (
        <div className="ch-ok"><span className="small">Waiting for your OK</span>
          <button className="btn small solid" onClick={() => onAct(() => api.post(`/chats/messages/${m.id}/approve`), 'Sent')}>Send it</button>
          <button className="btn small" onClick={() => onAct(() => api.post(`/chats/messages/${m.id}/reject`))}>Don’t send</button></div>
      )}
    </div>
  )
}

function NewChat({ onClose, onDone }) {
  const toast = useToast()
  const [f, setF] = useState({ phone: '', name: '', template: '', language: 'en_US', params: '' })
  const [busy, setBusy] = useState(false)
  const go = async (e) => {
    e.preventDefault(); setBusy(true)
    try { const r = await api.post('/chats/new', { phone: f.phone, name: f.name, template: f.template.trim(), language: f.language.trim() || 'en_US', params: f.params.split(',').map((x) => x.trim()).filter(Boolean) }); onDone(r.chat_id) } catch (x) { toast.err(x.message) } finally { setBusy(false) }
  }
  return (
    <div className="modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <form className="modal" onSubmit={go} role="dialog" aria-modal="true" aria-label="New WhatsApp chat" style={{ maxWidth: 480 }}>
        <h3>New WhatsApp chat</h3>
        <p className="muted small" style={{ margin: '6px 0 12px' }}>WhatsApp only lets a business start a conversation with an approved message template. Once they reply, you can write freely for 24 hours.</p>
        <label className="f">Their number <span className="faint">(with country code)</span><input autoFocus value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="447700900123" /></label>
        <label className="f">Their name <span className="faint">(optional)</span><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="f">Template Content SID or name<input value={f.template} onChange={(e) => setF({ ...f, template: e.target.value })} placeholder="e.g. appointment_reminder" /></label>
        <div style={{ display: 'flex', gap: 10 }}>
          <label className="f" style={{ width: 110 }}>Language<input value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })} /></label>
          <label className="f" style={{ flex: 1 }}>Values for {'{{1}}'}, {'{{2}}'}… <span className="faint">(commas)</span><input value={f.params} onChange={(e) => setF({ ...f, params: e.target.value })} /></label>
        </div>
        <div className="actions"><button className="btn solid" disabled={busy || f.phone.replace(/\D/g, '').length < 7 || !f.template.trim()}>{busy ? 'Sending…' : 'Send'}</button><button type="button" className="btn" onClick={onClose}>Cancel</button></div>
      </form>
    </div>
  )
}
