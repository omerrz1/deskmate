import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, usePoll } from './api'
import Icon from './Icon'
import { Skeleton } from './ui'
import { useConfirm, useToast } from './Feedback'

const TONES = ['#4f6d5a', '#7c4a8b', '#c45423', '#12806f', '#3b5bff', '#8a6d3b', '#a1355b', '#2f6f9f']
const tone = (s) => TONES[[...String(s || '?')].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % TONES.length]
const nameOf = (addr, team) => {
  const a = String(addr || '')
  const t = team.find((x) => x.email && a.toLowerCase().includes(x.email.toLowerCase()))
  if (t) return t.name
  const m = a.match(/^\s*"?([^"<]+?)"?\s*<.+>\s*$/)
  return m ? m[1] : a.split('@')[0] || a
}
const colorOf = (addr, team) => team.find((x) => x.email && String(addr || '').toLowerCase().includes(x.email.toLowerCase()))?.color || tone(addr)
const day = (ts) => {
  const d = new Date(ts * 1000), t = new Date(), y = new Date(Date.now() - 86400000)
  const same = (a, b) => a.toDateString() === b.toDateString()
  return same(d, t) ? 'Today' : same(d, y) ? 'Yesterday' : d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' })
}
const short = (ts) => {
  const d = new Date(ts * 1000)
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : d.toLocaleDateString([], { day: 'numeric', month: 'short' })
}
const full = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '')

/** Plain-text email body: web addresses become links, and quoted lines (starting with >) are set apart. */
function Body({ text }) {
  const lines = String(text || '').split('\n')
  const out = []
  let quote = []
  const flush = () => { if (quote.length) { out.push(<blockquote key={`q${out.length}`}>{quote.map((l, i) => <Fragment key={i}>{link(l.replace(/^>+\s?/, ''))}{'\n'}</Fragment>)}</blockquote>); quote = [] } }
  lines.forEach((l, i) => { if (/^>/.test(l)) quote.push(l); else { flush(); out.push(<Fragment key={i}>{link(l)}{'\n'}</Fragment>) } })
  flush()
  return <div className="mx-body">{out}</div>
}
function link(line) {
  const parts = line.split(/(https?:\/\/[^\s<>")]+)/g)
  return parts.map((p, i) => (/^https?:\/\//.test(p) ? <a key={i} href={p} target="_blank" rel="noopener noreferrer">{p}</a> : p))
}

const quoted = (m) => `\n\nOn ${full(m.created_at)}, ${m.from_addr} wrote:\n` + String(m.body || '').split('\n').map((l) => `> ${l}`).join('\n')
const DRAFT = 'deskmates-mail-draft'
const loadDraft = () => { try { return JSON.parse(localStorage.getItem(DRAFT) || 'null') } catch { return null } }
const saveDraft = (d) => { try { d ? localStorage.setItem(DRAFT, JSON.stringify(d)) : localStorage.removeItem(DRAFT) } catch { /* private mode */ } }

/** The mail client: folders, search, a day-grouped list, a reading pane with the whole conversation, bulk actions and keyboard shortcuts. */
export function Mail({ team, fixedBox, comfy = false }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [box, setBox] = useState(fixedBox ? String(fixedBox) : 'admin')
  const [folder, setFolder] = useState('inbox')
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  const [onlyUnread, setOnlyUnread] = useState(false)
  const [limit, setLimit] = useState(50)
  const [sel, setSel] = useState(() => new Set())
  const [open, setOpen] = useState(null)
  const [edit, setEdit] = useState(null)
  const [compose, setCompose] = useState(null)
  const [busy, setBusy] = useState(false)
  const admin = box === 'admin'
  const url = `/mail?box=${box}&folder=${folder}&q=${encodeURIComponent(dq)}&unread=${onlyUnread ? 1 : 0}&limit=${limit}`
  const [list, reload] = usePoll(url, 5000, [box, folder, dq, onlyUnread, limit])
  const [counts, reloadCounts] = usePoll(`/mail/counts?box=${box}`, 6000, [box])
  const [texts, reloadTexts] = usePoll('/outbox', admin ? 8000 : 0)
  const outbox = admin && Array.isArray(texts) ? texts : []
  const root = useRef(null)
  const [params, setParams] = useSearchParams()
  const want = !fixedBox ? params.get('open') : null

  useEffect(() => { if (want) { read(Number(want)); setParams({}, { replace: true }) } }, [want]) // eslint-disable-line
  useEffect(() => { const t = setTimeout(() => { setDq(q.trim()); setLimit(50) }, 250); return () => clearTimeout(t) }, [q])
  useEffect(() => { setSel(new Set()) }, [box, folder, dq, onlyUnread])

  const rows = list || []
  const refresh = useCallback(() => { reload(); reloadCounts(); reloadTexts() }, [reload, reloadCounts, reloadTexts])
  const read = useCallback(async (id) => {
    setEdit(null); setCompose(null)
    try { setOpen(await api.get(`/mail/${id}`)); refresh() } catch (e) { toast.err(e.message) }
  }, [refresh, toast])
  const act = async (ids, action, done) => {
    try { await api.post('/mail/bulk', { ids, action }); setSel(new Set()); if (done) done(); refresh() } catch (e) { toast.err(e.message) }
  }
  const remove = async (ids) => {
    if (!ids.length) return
    if (!(await confirm({ title: ids.length === 1 ? 'Delete this email?' : `Delete ${ids.length} emails?`, body: 'This can\'t be undone.', confirm: 'Delete', danger: true }))) return
    await act(ids, 'delete', () => { if (open && ids.includes(open.id)) setOpen(null) })
    toast.ok(ids.length === 1 ? 'Email deleted' : `${ids.length} emails deleted`)
  }
  const decide = async (m, approve) => {
    setBusy(true)
    try {
      if (approve && edit) await api.patch(`/mail/${m.id}`, { subject: edit.subject, body: edit.body })
      const r = await api.post(`/mail/${m.id}/${approve ? 'approve' : 'reject'}`)
      approve ? (r.sent ? toast.ok('Approved and sent') : toast.err(`Approved, but not delivered: ${r.error || 'unknown problem'}`)) : toast.ok('Rejected')
      setOpen(null); setEdit(null); refresh()
    } catch (e) { toast.err(e.message) } finally { setBusy(false) }
  }
  const decideText = async (o, approve) => {
    try {
      const r = await api.post(`/outbox/${o.id}/${approve ? 'approve' : 'reject'}`)
      approve ? (r.sent ? toast.ok('Sent') : toast.err(`Not sent: ${r.error}`)) : toast.ok('Rejected')
      reloadTexts()
    } catch (e) { toast.err(e.message) }
  }
  const startCompose = (init) => { setOpen(null); setEdit(null); setCompose(init || loadDraft() || { to: '', subject: '', body: '' }) }
  const send = async () => {
    if (!compose.to.trim()) { toast.err('Add who it is for.'); return }
    setBusy(true)
    try {
      const r = await api.post('/mail', compose)
      r.sent ? toast.ok('Sent') : toast.err(`Not delivered: ${r.error || 'unknown problem'}`)
      saveDraft(null); setCompose(null); refresh()
    } catch (e) { toast.err(e.message) } finally { setBusy(false) }
  }
  useEffect(() => { if (compose && (compose.to || compose.subject || compose.body) && !compose.reply_to) saveDraft(compose) }, [compose])

  // keyboard: j/k move, c writes, r replies, u marks unread, # deletes, esc goes back
  const move = (d) => {
    const i = rows.findIndex((r) => r.id === open?.id)
    const n = rows[Math.min(rows.length - 1, Math.max(0, i + d))]
    if (n) read(n.id)
  }
  useEffect(() => {
    const k = (e) => {
      if (!root.current || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'j') move(1)
      else if (e.key === 'k') move(-1)
      else if (e.key === 'c' && admin) { e.preventDefault(); startCompose() }
      else if (e.key === 'r' && open && admin && open.folder === 'inbox') { e.preventDefault(); reply(open) }
      else if (e.key === 'u' && open && open.folder === 'inbox') act([open.id], 'unread', () => setOpen(null))
      else if ((e.key === '#' || e.key === 'Delete') && open) remove([open.id])
      else if (e.key === 'Escape') { setOpen(null); setCompose(null); setEdit(null) }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  })

  const reply = (m) => startCompose({ to: m.from_addr, subject: /^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`, body: quoted(m), reply_to: m.id })
  const forward = (m) => startCompose({ to: '', subject: /^fwd?:/i.test(m.subject) ? m.subject : `Fwd: ${m.subject}`, body: `\n\n---------- Forwarded message ----------\nFrom: ${m.from_addr}\nDate: ${full(m.created_at)}\nSubject: ${m.subject}\n\n${m.body}` })

  const groups = useMemo(() => {
    const g = []
    rows.forEach((r) => { const d = day(r.created_at); if (!g.length || g[g.length - 1].d !== d) g.push({ d, items: [] }); g[g.length - 1].items.push(r) })
    return g
  }, [rows])

  const folders = [['inbox', 'Inbox', counts?.unread || 0], ['sent', 'Sent', 0], ['pending', 'To approve', (counts?.pending || 0) + outbox.length]]
  const allSel = rows.length > 0 && rows.every((r) => sel.has(r.id))
  const toggle = (id) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const showingTexts = folder === 'pending' && outbox.length > 0
  const pane = compose ? 'compose' : open ? 'open' : ''

  return (
    <div className="mx-wrap"><div className={`mx ${comfy ? 'mx-lg' : ''} ${pane ? 'has-open' : ''}`} ref={root}>
      <aside className="mx-rail">
        {admin && <button className="btn solid mx-write" onClick={() => startCompose()}><Icon name="edit" size={14} /> Write</button>}
        {!fixedBox && (
          <label className="mx-box"><span>Mailbox</span>
            <select value={box} onChange={(e) => { setBox(e.target.value); setOpen(null); setCompose(null); setFolder('inbox') }}>
              <option value="admin">Mine (admin)</option>{team.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </label>
        )}
        <nav>
          {folders.map(([k, n, c]) => (
            <button key={k} className={`mx-fold ${folder === k ? 'on' : ''}`} onClick={() => { setFolder(k); setOpen(null); setCompose(null) }}>
              <Icon name={k === 'inbox' ? 'inbox' : k === 'sent' ? 'mail' : 'check'} size={15} /><span>{n}</span>{c > 0 && <i className={k === 'pending' ? 'warm' : ''}>{c}</i>}
            </button>
          ))}
        </nav>
        <p className="mx-keys faint">Shortcuts: <kbd>j</kbd> <kbd>k</kbd> move, <kbd>c</kbd> write, <kbd>r</kbd> reply, <kbd>#</kbd> delete</p>
      </aside>

      <section className="mx-list">
        <div className="mx-tools">
          <div className="mx-search"><Icon name="search" size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search mail" aria-label="Search mail" />{q && <button onClick={() => setQ('')} aria-label="Clear search"><Icon name="x" size={13} /></button>}</div>
          {folder === 'inbox' && <button className={`chip ${onlyUnread ? 'on' : ''}`} onClick={() => setOnlyUnread(!onlyUnread)}>Unread</button>}
        </div>
        {sel.size > 0 ? (
          <div className="mx-bulk">
            <label className="mx-ck"><input type="checkbox" checked={allSel} onChange={() => setSel(allSel ? new Set() : new Set(rows.map((r) => r.id)))} /></label>
            <b>{sel.size} selected</b>
            {folder === 'inbox' && <><button className="link" onClick={() => act([...sel], 'read')}>Mark read</button><button className="link" onClick={() => act([...sel], 'unread')}>Mark unread</button></>}
            <button className="link danger" onClick={() => remove([...sel])}>Delete</button>
          </div>
        ) : rows.length > 0 && <div className="mx-bulk quiet"><label className="mx-ck"><input type="checkbox" checked={false} onChange={() => setSel(new Set(rows.map((r) => r.id)))} aria-label="Select all" /></label><span className="faint small">{rows.length}{rows.length >= limit ? '+' : ''} {dq ? 'found' : 'emails'}</span></div>}
        <div className="mx-scroll">
          {showingTexts && (
            <div className="mx-texts">
              <h4>Texts and WhatsApp to approve</h4>
              {outbox.map((o) => (
                <div key={o.id} className="mx-text">
                  <div><b>{o.channel === 'sms' ? 'Text' : 'WhatsApp'} to {o.to_addr}</b><span className="faint small"> from {o.agent_name || 'a call'}</span><p>{o.body}</p></div>
                  <div className="actions"><button className="btn small solid" onClick={() => decideText(o, true)}>Approve</button><button className="btn small" onClick={() => decideText(o, false)}>Reject</button></div>
                </div>
              ))}
            </div>
          )}
          {!list ? <div style={{ padding: 12 }}><Skeleton rows={5} /></div> : rows.length === 0 ? (
            !showingTexts && <div className="mx-empty"><Icon name={folder === 'pending' ? 'check' : 'inbox'} size={22} /><b>{dq ? 'No matches' : folder === 'pending' ? 'Nothing to approve' : folder === 'sent' ? 'Nothing sent yet' : 'Inbox zero'}</b><p className="small">{dq ? `Nothing found for "${dq}".` : folder === 'pending' ? 'Emails your team wants to send outside the company wait here.' : folder === 'sent' ? 'Emails sent from this mailbox show up here.' : 'New messages appear here.'}</p></div>
          ) : groups.map((g) => (
            <div key={g.d}>
              <h5 className="mx-day">{g.d}</h5>
              {g.items.map((m) => {
                const who = folder === 'sent' || folder === 'pending' ? m.to_addr : m.from_addr
                const unread = m.status === 'unread'
                return (
                  <div key={m.id} className={`mx-row ${unread ? 'unread' : ''} ${open?.id === m.id ? 'on' : ''} ${sel.has(m.id) ? 'sel' : ''}`}>
                    <label className="mx-ck" onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={sel.has(m.id)} onChange={() => toggle(m.id)} aria-label="Select" /></label>
                    <button className="mx-open" onClick={() => read(m.id)}>
                      <span className="mx-av" style={{ background: colorOf(who, team) }}>{nameOf(who, team).slice(0, 1).toUpperCase()}</span>
                      <span className="mx-main">
                        <span className="mx-top"><b>{folder === 'sent' || folder === 'pending' ? `To ${nameOf(who, team)}` : nameOf(who, team)}</b>
                          {m.thread_count > 1 && <em>{m.thread_count}</em>}
                          {m.has_attachments && <Icon name="file" size={12} />}
                          <time>{short(m.created_at)}</time></span>
                        <span className="mx-sub">{m.subject || '(no subject)'}</span>
                        <span className="mx-pre">{folder === 'pending' && m.owner ? `${m.owner} wants to send this · ` : ''}{m.status === 'failed' ? 'Not delivered · ' : ''}{m.preview}</span>
                      </span>
                      {unread && <i className="mx-dot" aria-label="Unread" />}
                    </button>
                  </div>
                )
              })}
            </div>
          ))}
          {rows.length >= limit && <button className="btn small mx-more" onClick={() => setLimit(limit + 50)}>Load more</button>}
        </div>
      </section>

      <section className="mx-pane">
        {compose ? (
          <div className="mx-compose">
            <header><h3>{compose.reply_to ? 'Reply' : 'New email'}</h3><button className="icon-x" onClick={() => setCompose(null)} aria-label="Close"><Icon name="x" size={16} /></button></header>
            <label className="mx-f"><span>To</span><input list="mx-addr" value={compose.to} onChange={(e) => setCompose({ ...compose, to: e.target.value })} placeholder="A teammate or any address" autoFocus={!compose.to} /></label>
            <datalist id="mx-addr">{team.filter((a) => a.email).map((a) => <option key={a.id} value={a.email}>{a.name}</option>)}</datalist>
            <label className="mx-f"><span>Subject</span><input value={compose.subject} onChange={(e) => setCompose({ ...compose, subject: e.target.value })} /></label>
            <textarea className="mx-text-in" value={compose.body} onChange={(e) => setCompose({ ...compose, body: e.target.value })} placeholder="Write your message" autoFocus={!!compose.to} onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); send() } }} />
            <footer>
              <button className="btn solid" disabled={busy} onClick={send}>{busy ? 'Sending…' : 'Send'}</button>
              <button className="btn ghost" onClick={() => { saveDraft(null); setCompose(null) }}>Discard</button>
              <span className="faint small">{compose.reply_to ? '' : 'Draft saved on this device · '}⌘↵ to send</span>
            </footer>
          </div>
        ) : open ? (
          <article className="mx-msg">
            <header>
              <button className="back mx-back" onClick={() => setOpen(null)}><Icon name="back" size={14} /> Back</button>
              <h2>{open.subject || '(no subject)'}</h2>
              <div className="mx-meta">
                <span className="mx-av lg" style={{ background: colorOf(open.from_addr, team) }}>{nameOf(open.from_addr, team).slice(0, 1).toUpperCase()}</span>
                <div><b>{nameOf(open.from_addr, team)}</b> <span className="faint small">{open.from_addr}</span><br /><span className="faint small">to {open.to_addr}{open.cc ? `, cc ${open.cc}` : ''} · {full(open.created_at)}{open.external ? ' · outside the company' : ''}</span></div>
              </div>
              <div className="mx-acts">
                {admin && open.folder === 'inbox' && <button className="btn small" onClick={() => reply(open)}><Icon name="back" size={13} /> Reply</button>}
                {admin && <button className="btn small" onClick={() => forward(open)}>Forward</button>}
                {open.folder === 'inbox' && <button className="btn small" onClick={() => act([open.id], 'unread', () => setOpen(null))}>Mark unread</button>}
                <button className="btn small danger" onClick={() => remove([open.id])}><Icon name="trash" size={13} /> Delete</button>
              </div>
            </header>
            {open.status === 'pending_approval' && (
              <div className="mx-approve">
                <p><b>{open.owner || 'A teammate'}</b> wants to send this to <b>{open.to_addr}</b>{open.external ? ', outside the company' : ''}. It goes out only if you approve.{open.hold_reason && <><br /><span className="small"><b>Held because:</b> {open.hold_reason}</span></>}</p>
                <div className="actions">
                  <button className="btn solid" disabled={busy} onClick={() => decide(open, true)}>{edit ? 'Save and send' : 'Approve and send'}</button>
                  {!edit ? <button className="btn" onClick={() => setEdit({ subject: open.subject, body: open.body })}>Edit first</button> : <button className="btn" onClick={() => setEdit(null)}>Cancel edit</button>}
                  <button className="btn danger" disabled={busy} onClick={() => decide(open, false)}>Reject</button>
                </div>
              </div>
            )}
            {open.error && <p className="warn">Delivery problem: {open.error}</p>}
            {edit ? (
              <div className="mx-edit"><label className="mx-f"><span>Subject</span><input value={edit.subject} onChange={(e) => setEdit({ ...edit, subject: e.target.value })} /></label><textarea className="mx-text-in" value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} /></div>
            ) : <Body text={open.body} />}
            {open.attachments?.length > 0 && <div className="mx-att">{open.attachments.map((f) => <a key={f.id} className="btn small" href={`/api/docs/${f.id}/download`}><Icon name="file" size={13} /> {f.title}</a>)}</div>}
            {open.thread?.length > 0 && (
              <div className="mx-thread"><h4>Earlier in this conversation ({open.thread.length})</h4>
                {open.thread.map((t) => (
                  <details key={t.id}><summary><b>{nameOf(t.from_addr, team)}</b> <span className="faint small">{full(t.created_at)}</span></summary><Body text={t.body} /></details>
                ))}
              </div>
            )}
          </article>
        ) : (
          <div className="mx-none"><Icon name="mail" size={26} /><p>Select an email to read it</p><span className="faint small">{admin ? 'or press c to write one' : 'You are looking at this mailbox read-only.'}</span></div>
        )}
      </section>
    </div></div>
  )
}
