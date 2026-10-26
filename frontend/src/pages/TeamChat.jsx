import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, downloadFile, stream, usePoll } from '../api'
import Avatar from '../Avatar'
import Icon from '../Icon'
import { useTitle } from '../ui'
import { useConfirm, usePrompt, useToast } from '../Feedback'

function chatTitle(title = '') { return title.startsWith('Chat: ') ? `Chat with ${title.slice(6)}` : title || 'New chat' }
function stamp(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '' }
function day(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000), t = new Date()
  if (d.toDateString() === t.toDateString()) return stamp(ts)
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' })
}
const idsOf = (c) => { try { const v = typeof c.agent_ids === 'string' ? JSON.parse(c.agent_ids) : c.agent_ids; return Array.isArray(v) ? v : [] } catch { return [] } }

/** Overlapping faces of the teammates in a chat. */
function Faces({ people, size = 32 }) {
  return <span className="chat-faces">{people.slice(0, 3).map((a) => <Avatar key={a.id} agent={a} size={size} />)}{people.length > 3 && <i style={{ width: size, height: size }}>+{people.length - 3}</i>}</span>
}

export default function TeamChat() {
  useTitle('Chat')
  const confirm = useConfirm()
  const prompt = usePrompt()
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const id = params.get('id')
  const [agents] = usePoll('/agents', 0)
  const [chats, reload] = usePoll('/meetings?chat=true', 0)
  const [room, setRoom] = useState(null)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState(() => params.get('agent') ? [Number(params.get('agent'))] : [])
  const [text, setText] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [partial, setPartial] = useState(null)
  const [error, setError] = useState('')
  const [menu, setMenu] = useState(false)
  const [creating, setCreating] = useState(false)
  const [dragging, setDragging] = useState(false)
  const abort = useRef(null)
  const lock = useRef(false)
  const bottom = useRef(null)
  const uploadLock = useRef(false)
  const picker = useRef(null)
  const composer = useRef(null)
  useEffect(() => { if (composer.current) { composer.current.style.height = 'auto'; composer.current.style.height = `${Math.min(200, Math.max(24, composer.current.scrollHeight))}px` } }, [text, room?.id])
  useEffect(() => {
    let alive = true
    setRoom(null); setPartial(null); setError(''); setText(''); setMenu(false)
    if (id) api.get(`/meetings/${id}`).then((r) => { if (alive) { if (r.kind === 'admin_chat') setRoom(r); else setError('Choose a team chat from the list.') } }).catch((e) => { if (alive) setError(e.message) })
    return () => { alive = false; abort.current?.abort() }
  }, [id])
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [room?.messages, partial])
  // A call a teammate placed reports back into this chat when it ends, minutes after the message that asked for it: pick it up without a reload.
  const roomId = room?.id
  useEffect(() => {
    if (!roomId) return undefined
    const timer = setInterval(async () => {
      if (lock.current || uploadLock.current || document.hidden) return
      try {
        const fresh = await api.get(`/meetings/${roomId}`)
        if (lock.current) return
        setRoom((old) => (old && old.id === fresh.id && fresh.messages.at(-1)?.id !== old.messages.at(-1)?.id ? fresh : old))
      } catch { /* offline for a moment: the next tick tries again */ }
    }, 5000)
    return () => clearInterval(timer)
  }, [roomId])
  async function create() {
    if (!selected.length || lock.current) return
    lock.current = true; setBusy(true); setError('')
    try { const r = await api.post('/meetings', { agent_ids: selected, title: newTitle.trim(), chat: true }); await reload(); setParams({ id: r.id }); setSelected([]); setNewTitle(''); setCreating(false) }
    catch (e) { setError(e.message) } finally { lock.current = false; setBusy(false) }
  }
  async function renameCurrent() {
    const next = await prompt({ title: 'Name this chat', label: 'Chat name', value: room?.title || '', placeholder: 'e.g. Spring campaign planning', confirm: 'Save name' })
    if (!next) return
    try { const r = await api.patch(`/meetings/${id}`, { title: next }); setRoom((old) => ({ ...old, title: r.title })); await reload(); toast.ok('Chat renamed') }
    catch (e) { setError(e.message) }
  }
  async function removeChat(chatId, title) {
    if (!(await confirm({ title: 'Delete this chat?', body: `“${chatTitle(title)}” and its messages will be permanently removed. Shared-drive files will stay in the drive.`, confirm: 'Delete chat', danger: true }))) return
    try { await api.del(`/meetings/${chatId}`); if (String(chatId) === id) { abort.current?.abort(); setRoom(null); setParams({}) } await reload(); toast.ok('Chat deleted') }
    catch (e) { setError(e.message) }
  }
  async function refreshRoom() { const r = await api.get(`/meetings/${id}`); setRoom(r) }
  async function send(e) {
    e.preventDefault()
    const message = text.trim()
    if (!message || !room || lock.current || uploadLock.current) return
    lock.current = true; setBusy(true); setText(''); setError(''); abort.current = new AbortController()
    setRoom((r) => ({ ...r, messages: [...r.messages, { id: `u-${Date.now()}`, role: 'admin', text: message }] }))
    try {
      await stream(`/meetings/${id}/message`, { headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: message }) }, (ev) => {
        if (ev.type === 'speaker') setPartial({ agent_id: ev.agent_id, text: '', status: 'Thinking' })
        if (ev.type === 'token') setPartial((p) => ({ agent_id: ev.agent_id, text: (p?.text || '') + ev.text }))
        if (ev.type === 'tool_start') setPartial((p) => ({ ...p, status: 'Working' }))
        if (ev.type === 'agent_done') { setRoom((r) => ({ ...r, messages: [...r.messages, { id: `a-${Date.now()}-${ev.agent_id}`, role: 'agent', agent_id: ev.agent_id, text: ev.text, tools: ev.tools }] })); setPartial(null) }
        if (ev.type === 'agent_pass') setPartial(null)
        if (ev.type === 'error') setError(ev.message)
      }, abort.current.signal)
      await refreshRoom()
    } catch (e) { if (e.name !== 'AbortError') { setError(e.message); setText(message) } }
    finally { lock.current = false; setBusy(false); setPartial(null) }
  }
  async function upload(list) {
    if (!list?.length || !room || uploadLock.current || room.status === 'ended') return
    uploadLock.current = true
    setUploading(true); setError('')
    try {
      for (const file of Array.from(list)) {
        const fd = new FormData(); fd.append('owner', 'shared'); fd.append('file', file)
        const saved = await api.form('/fs/upload', fd)
        await api.post(`/meetings/${id}/attachments`, { doc_id: saved.id })
      }
      await refreshRoom()
    } catch (e) { setError(e.message) }
    finally { uploadLock.current = false; setUploading(false); if (picker.current) picker.current.value = '' }
  }
  // the server names the file (a CSV is saved as .csv, an Excel file as .xlsx); format: 'csv' | 'xlsx' picks one for a sheet
  async function download(docId, title, format) {
    try { await downloadFile(`/api/docs/${docId}/download${format ? `?format=${format}` : ''}`, title || 'download') } catch (e) { setError(e.message) }
  }
  const who = (aid) => room?.agents.find((a) => a.id === aid)?.name || 'Teammate'
  const filteredChats = (chats || []).filter((c) => chatTitle(c.title).toLowerCase().includes(filter.toLowerCase()))
  const peopleOf = (c) => { const ids = idsOf(c); return (agents || []).filter((a) => ids.includes(a.id)) }
  const ended = room?.status === 'ended'
  const startNew = () => { setSelected([]); setCreating(true); setParams({}) }
  const dropFiles = (e) => { setDragging(false); if (e.dataTransfer.files.length) { e.preventDefault(); upload(e.dataTransfer.files) } }
  // a message starts a new group when the sender changes or a few minutes have passed
  const startsGroup = (m, i, all) => { const prev = all[i - 1]; return !prev || prev.role !== m.role || prev.agent_id !== m.agent_id || (m.created_at && prev.created_at && m.created_at - prev.created_at > 300) }
  return <div className="page chat-page">
    <div className={`chat-layout ${id ? 'in-room' : creating ? 'creating' : ''}`}>
      <aside className="chat-side">
        <div className="chat-side-head"><h1>Chat</h1><button className="btn solid small" onClick={startNew}><Icon name="plus" size={14} /> New chat</button></div>
        <label className="chat-search"><Icon name="search" size={15} /><input aria-label="Search chats" placeholder="Search chats" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
        <div className="chat-list">
          {filteredChats.map((c) => <button key={c.id} className={`chat-item ${String(c.id) === id ? 'on' : ''}`} onClick={() => { setCreating(false); setParams({ id: c.id }) }}>
            <Faces people={peopleOf(c)} size={30} /><span className="chat-item-text"><b>{chatTitle(c.title)}</b><small>{peopleOf(c).map((a) => a.name).join(', ') || 'Your team'}</small></span><time>{day(c.created_at)}</time>
          </button>)}
          {!filteredChats.length && <p className="chat-none">{filter ? 'No chats match that.' : 'Your chats will show up here.'}</p>}
        </div>
      </aside>
      <section className={`chat-main ${dragging ? 'dragging' : ''}`} onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); if (id) setDragging(true) } }} onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false) }} onDrop={dropFiles}>
        {!room && error && <div className="chat-error" role="alert">{error}</div>}
        {!id ? <div className="chat-new">
          <button className="chat-back" onClick={() => setCreating(false)} aria-label="Back to all chats"><Icon name="back" size={18} /></button>
          <h2>Who do you want to talk to?</h2>
          <p>Pick one or more people from your team. You can share files in the chat.</p>
          <div className="chat-pick">{(agents || []).map((a) => {
            const on = selected.includes(a.id)
            return <label key={a.id} className={on ? 'picked' : ''}><input type="checkbox" checked={on} disabled={!on && selected.length >= 6} onChange={(e) => setSelected((x) => e.target.checked ? [...x, a.id] : x.filter((i) => i !== a.id))} /><Avatar agent={a} size={40} /><span className="pick-text"><b>{a.name}</b><small>{a.role}</small></span><i>{on ? <Icon name="check" size={14} /> : null}</i></label>
          })}</div>
          {selected.length > 0 && <label className="chat-name">Name this chat <span className="muted">(optional)</span><input maxLength={80} placeholder={selected.length === 1 ? `Chat with ${agents?.find((a) => a.id === selected[0])?.name || 'your teammate'}` : 'e.g. Spring campaign'} value={newTitle} onChange={(e) => setNewTitle(e.target.value)} /></label>}
          {agents?.length === 0 && <p><Link to="/">Add someone to your team first</Link></p>}
          <button className="btn solid chat-go" onClick={create} disabled={busy || !selected.length}>Start chatting</button>
        </div> : !room ? <div className="chat-loading"><i /><span>Opening chat…</span></div> : <>
          <header className="chat-head">
            <button className="chat-back" onClick={() => setParams({})} aria-label="Back to all chats"><Icon name="back" size={18} /></button>
            <Faces people={room.agents} size={34} />
            <div className="chat-head-text"><h2>{chatTitle(room.title)}</h2><span>{room.agents.map((a) => a.name).join(', ')}</span></div>
            <div className="chat-menu-wrap">
              <button className="chat-more" onClick={() => setMenu(!menu)} aria-label="More" aria-expanded={menu}><Icon name="more" size={18} /></button>
              {menu && <><div className="chat-menu-bg" onClick={() => setMenu(false)} /><div className="chat-menu"><button onClick={() => { setMenu(false); renameCurrent() }}>Rename chat</button><button className="danger" onClick={() => { setMenu(false); removeChat(room.id, room.title) }}>Delete chat</button></div></>}
            </div>
          </header>
          <div className="chat-scroll" aria-live="polite" aria-relevant="additions text">
            <div className="chat-thread">
              {!room.messages.length && <div className="chat-empty"><h3>Say hello</h3><p>Ask a question, share a file, or give them some work.</p></div>}
              {room.messages.map((m, i, all) => {
                const mine = m.role === 'admin', first = startsGroup(m, i, all), agent = room.agents.find((a) => a.id === m.agent_id)
                return <article key={m.id} className={`chat-msg ${mine ? 'mine' : ''} ${first ? 'first' : ''}`}>
                  {!mine && <span className="chat-msg-av">{first && agent && <Avatar agent={agent} size={32} />}</span>}
                  <div className="chat-msg-body">
                    {first && <div className="chat-meta"><b>{mine ? 'You' : who(m.agent_id)}</b><time>{stamp(m.created_at) || (i === all.length - 1 ? 'Now' : '')}</time></div>}
                    {m.text && <div className="chat-bubble">{m.text}</div>}
                    {m.attachments?.length > 0 && <div className="chat-files">{m.attachments.map((f) => <div className="chat-file" key={f.doc_id}>
                      <button className="chat-file-main" onClick={() => download(f.doc_id, f.title)} title="Download"><span className="chat-file-ico"><Icon name={f.kind === 'sheet' ? 'sheet' : 'file'} size={20} /></span><span className="chat-file-info"><b>{f.title}</b><small>{(f.format || (f.kind === 'sheet' ? 'sheet' : f.kind) || 'file').toUpperCase()}{f.size ? ` · ${Math.max(1, Math.round(f.size / 1024))} KB` : ''}</small></span><Icon name="download" size={16} /></button>
                      {f.kind === 'sheet' && <span className="chat-file-formats"><button title="Download as a CSV file" onClick={() => download(f.doc_id, f.title, 'csv')}>CSV</button><button title="Download as an Excel file" onClick={() => download(f.doc_id, f.title, 'xlsx')}>Excel</button></span>}
                    </div>)}</div>}
                  </div>
                </article>
              })}
              {partial && <article className="chat-msg first"><span className="chat-msg-av"><Avatar agent={room.agents.find((a) => a.id === partial.agent_id)} size={32} /></span><div className="chat-msg-body"><div className="chat-meta"><b>{who(partial.agent_id)}</b></div><div className="chat-bubble">{partial.text || <span className="chat-typing"><i /><i /><i /> {partial.status}…</span>}</div></div></article>}
              <div ref={bottom} />
            </div>
          </div>
          {error && <div className="chat-error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss">×</button></div>}
          {ended ? <p className="chat-ended">This chat has ended. Start a new one to keep talking.</p> : <form className="chat-compose" onSubmit={send}>
            <div className="chat-box">
              <textarea ref={composer} aria-label="Message your team" placeholder="Write a message…" value={text} onChange={(e) => setText(e.target.value)} rows={1} maxLength={4000} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form.requestSubmit() } }} />
              <input ref={picker} type="file" multiple accept=".pdf,.docx,.txt,.md,.json,.csv,.tsv,.tab,.xlsx,.xls" hidden onChange={(e) => upload(e.target.files)} />
              <button type="button" className="chat-attach" onClick={() => picker.current?.click()} disabled={uploading} title="Attach a file" aria-label="Attach a file"><Icon name="upload" size={18} /><span>{uploading ? 'Saving…' : 'Attach'}</span></button>
              <button className="chat-send" disabled={busy || uploading || !text.trim()} aria-label="Send message"><Icon name="up" size={18} /></button>
            </div>
            <p className="chat-hint">{text.length > 3500 ? `${text.length} of 4,000 letters · ` : ''}Press Enter to send. Shift + Enter starts a new line. Drop a file anywhere to share it.</p>
          </form>}
          {dragging && <div className="chat-drop">Drop to share with this chat</div>}
        </>}
      </section>
    </div>
  </div>
}
