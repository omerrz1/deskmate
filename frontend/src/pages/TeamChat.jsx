import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, apiFetch, stream, usePoll } from '../api'
import Avatar from '../Avatar'
import { useTitle } from '../ui'
import { useConfirm, usePrompt, useToast } from '../Feedback'

function initials(title = '') { return title.replace(/^Chat: /, '').split(/[ ,]/).filter(Boolean).slice(0, 2).map((x) => x[0]).join('').toUpperCase() }
function chatTitle(title = '') { return title.startsWith('Chat: ') ? `Chat with ${title.slice(6)}` : title || 'New chat' }
function stamp(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '' }

export default function TeamChat() {
  useTitle('Team chat')
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
  const abort = useRef(null)
  const lock = useRef(false)
  const bottom = useRef(null)
  const uploadLock = useRef(false)
  const picker = useRef(null)
  const composer = useRef(null)
  useEffect(() => { if (composer.current) { composer.current.style.height = 'auto'; composer.current.style.height = `${Math.min(240, Math.max(112, composer.current.scrollHeight))}px` } }, [text, room?.id])
  useEffect(() => {
    let alive = true
    setRoom(null); setPartial(null); setError(''); setText('')
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
    try { const r = await api.post('/meetings', { agent_ids: selected, title: newTitle.trim(), chat: true }); await reload(); setParams({ id: r.id }); setSelected([]); setNewTitle('') }
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
  async function download(docId) {
    try {
      const r = await apiFetch(`/api/docs/${docId}/download`)
      if (!r.ok) throw new Error('Could not download this file')
      const blob = await r.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a')
      a.href = url; a.download = ''; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) { setError(e.message) }
  }
  const who = (aid) => room?.agents.find((a) => a.id === aid)?.name || 'Teammate'
  const filteredChats = (chats || []).filter((c) => c.title.toLowerCase().includes(filter.toLowerCase()))
  const fileOnly = (m) => !m.text && m.attachments?.length
  return <div className="page wide team-chat-page">
    <div className="team-chat-layout">
      <aside className="team-chat-list">
        <div className="tc-list-top"><div><span className="tc-eyebrow">WORKSPACE</span><h1>Messages</h1></div><button className="tc-new" onClick={() => { setSelected([]); setParams({}) }} title="New chat" aria-label="New chat">＋</button></div>
        <label className="tc-search"><span>⌕</span><input aria-label="Search chats" placeholder="Search chats" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
        <div className="tc-section-label">CHATS</div>
        <div className="tc-threads">
          {filteredChats.map((c) => <div key={c.id} className={`tc-thread-row ${String(c.id) === id ? 'active' : ''}`}><button className="tc-thread" onClick={() => setParams({ id: c.id })}>
            <span className="tc-thread-icon">{initials(chatTitle(c.title)) || '•'}</span><span className="tc-thread-copy"><b>{chatTitle(c.title)}</b><small>{stamp(c.created_at)}</small></span>
          </button><button className="tc-thread-delete" onClick={() => removeChat(c.id, c.title)} aria-label={`Delete ${chatTitle(c.title)}`} title="Delete chat">×</button></div>)}
          {!filteredChats.length && <div className="tc-no-chats">{filter ? 'No matching chats' : 'Your conversations will show here'}</div>}
        </div>
        <button className="tc-create-link" onClick={() => { setSelected([]); setParams({}) }}>＋ <span>Start a new chat</span></button>
      </aside>
      <section className="team-chat-room" onDragOver={e => { if (e.dataTransfer.types.includes('Files')) e.preventDefault() }} onDrop={e => { if (e.dataTransfer.files.length) { e.preventDefault(); upload(e.dataTransfer.files) } }}>
        {!room && error && <div className="tc-error" role="alert">{error}</div>}
        {!id ? <div className="tc-new-room"><span className="tc-eyebrow">NEW CONVERSATION</span><h2>Who would you like to chat with?</h2><p>Pick one or more teammates. You can share files in the conversation.</p>
          <div className="tc-agent-picker">{(agents || []).map((a) => <label key={a.id} className={selected.includes(a.id) ? 'picked' : ''}><input type="checkbox" checked={selected.includes(a.id)} disabled={!selected.includes(a.id) && selected.length >= 6} onChange={(e) => setSelected((s) => e.target.checked ? [...s, a.id] : s.filter((i) => i !== a.id))} /><Avatar agent={a} size={36} /><span><b>{a.name}</b><small>{a.role}</small></span><i>{selected.includes(a.id) ? '✓' : ''}</i></label>)}</div>
          {selected.length > 0 && <label className="tc-title-field">Name this chat <span className="muted">(optional)</span><input maxLength={80} placeholder={selected.length === 1 ? `Chat with ${agents?.find(a => a.id === selected[0])?.name || 'your teammate'}` : 'e.g. Product launch planning'} value={newTitle} onChange={e => setNewTitle(e.target.value)}/></label>}
          {agents?.length === 0 && <p><Link to="/">Add teammates first</Link></p>}<button className="btn solid tc-start" onClick={create} disabled={busy || !selected.length}>Start chat</button>
        </div> : !room ? <div className="team-chat-loading"><i/><span>Opening chat…</span></div> : <>
          <header className="tc-room-head"><div className="tc-room-avatars">{room.agents.slice(0, 4).map((a) => <Avatar key={a.id} agent={a} size={34}/>)}</div><div className="tc-room-heading"><h2>{chatTitle(room.title)}</h2><span>{room.agents.map((a) => a.name).join(', ')}</span></div><button className="tc-room-action" onClick={renameCurrent}>Rename</button><button className="tc-room-action danger" onClick={() => removeChat(room.id, room.title)}>Delete</button><span className="tc-room-status"><i/> Team chat</span></header>
          <div className="tc-transcript" aria-live="polite" aria-relevant="additions text">
            {!room.messages.length && <div className="tc-empty"><div className="tc-empty-icon">✳</div><h3>Start with a message</h3><p>Ask a question, share a document, or work through an idea together.</p></div>}
            {room.messages.map((m, i) => <article key={m.id} className={`tc-message ${m.role === 'admin' ? 'mine' : ''} ${fileOnly(m) ? 'attachment-only' : ''}`}>
              {m.role !== 'admin' && <Avatar agent={room.agents.find((a) => a.id === m.agent_id)} size={28}/>}
              <div className="tc-bubble-wrap"><div className="tc-meta"><b>{m.role === 'admin' ? 'You' : who(m.agent_id)}</b><time>{stamp(m.created_at) || (i === room.messages.length - 1 ? 'Now' : '')}</time></div>
                {m.text && <div className="tc-bubble">{m.text}</div>}
                {m.attachments?.length > 0 && <div className="tc-files">{m.attachments.map((f) => <button className="tc-file" key={f.doc_id} onClick={() => download(f.doc_id)}><span className="tc-file-icon">↧</span><span className="tc-file-info"><b>{f.title}</b><small>{(f.kind || 'file').toUpperCase()} · {f.size ? `${Math.max(1, Math.round(f.size / 1024))} KB` : 'Shared file'}{f.saved_in ? ` · Saved in ${f.saved_in}` : ''}</small></span><span className="tc-file-download">↓</span></button>)}</div>}
              </div>
            </article>)}
            {partial && <article className="tc-message"><Avatar agent={room.agents.find((a) => a.id === partial.agent_id)} size={28}/><div className="tc-bubble-wrap"><div className="tc-meta"><b>{who(partial.agent_id)}</b></div><div className="tc-bubble">{partial.text || <span className="tc-typing"><i/><i/><i/> {partial.status}…</span>}</div></div></article>}
            <div ref={bottom}/>
          </div>
          {error && <div className="tc-error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss">×</button></div>}
          <form className="tc-compose" onSubmit={send}><div className="tc-compose-box"><textarea ref={composer} aria-label="Message your teammates" placeholder={room.status === 'ended' ? 'This chat has ended' : 'Write a message…'} value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={4000} disabled={room.status === 'ended'} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form.requestSubmit() } }}/>
            <div className="tc-compose-actions"><input ref={picker} type="file" multiple accept=".pdf,.docx,.txt,.md,.json,.csv,.xlsx,.xls" hidden onChange={(e) => upload(e.target.files)}/><button type="button" className="tc-attach" onClick={() => picker.current?.click()} disabled={uploading || room.status === 'ended'} title="Attach a file">{uploading ? '…' : '＋'} <span>{uploading ? 'Saving files…' : 'Attach'}</span></button><span className="tc-hint">{text.length > 3500 ? `${text.length}/4000 · ` : ''}Drop files here · Saved on teammates’ computers</span><button className="tc-send" disabled={busy || uploading || !text.trim() || room.status === 'ended'} aria-label="Send message">↑</button></div>
          </div></form>
        </>}
      </section>
    </div>
  </div>
}
