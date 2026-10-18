import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, apiFetch, stream } from '../api'
import Avatar from '../Avatar'
import { createRT } from '../meetingRT'

const TOOL_TEXT = {
  weekly_report: 'pulling the report', get_dashboard_stats: 'checking the numbers', list_campaigns: 'looking at campaigns', create_campaign: 'setting up a campaign',
  start_campaign: 'starting a campaign', assign_duty: 'assigning a duty', update_my_profile: 'rewriting their profile', learn_from_website: 'reading a website',
  create_knowledge_base: 'creating a knowledge base', add_knowledge: 'writing knowledge', update_knowledge_base: 'updating knowledge', remember: 'making a note',
  list_calls: 'reading recent calls', get_call: 'opening a call', get_client_report: 'writing a client report', ask_colleague: 'asking a colleague',
  create_agent: 'hiring someone', schedule_call: 'scheduling a call', search_contacts: 'searching customers',
}
const hidden = (t) => /^\[The admin has just joined/.test(t || '')
const I = {
  mic: <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />,
  cc: <path d="M19 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Zm-8 7H9.5v-.5h-2v3h2V13H11v1a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1Zm7 0h-1.5v-.5h-2v3h2V13H18v1a1 1 0 0 1-1 1h-3a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1Z" />,
  chat: <path d="M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2Z" />,
  people: <path d="M16 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm-8 0a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm0 2c-2.3 0-7 1.2-7 3.5V19h14v-2.5C15 14.2 10.3 13 8 13Zm8 0c-.3 0-.6 0-1 .1 1.2.9 2 2 2 3.4V19h6v-2.5c0-2.3-4.7-3.5-7-3.5Z" />,
  end: <path d="M12 9c-1.6 0-3.2.3-4.6.7v2.7c0 .3-.2.6-.5.7-1 .3-1.8.9-2.6 1.6-.4.4-1 .4-1.4 0L.3 13.4a1 1 0 0 1 0-1.4C3.2 9.1 7.4 7.5 12 7.5s8.8 1.6 11.7 4.5a1 1 0 0 1 0 1.4l-2.6 2.6c-.4.4-1 .4-1.4 0-.8-.7-1.6-1.3-2.6-1.6-.3-.1-.5-.4-.5-.7V9.7C15.2 9.3 13.6 9 12 9Z" />,
}
const Icon = ({ n, size = 22 }) => <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">{I[n]}</svg>

export default function Meeting() {
  const { id } = useParams()
  const nav = useNavigate()
  const [m, setM] = useState(null)
  const [msgs, setMsgs] = useState([])
  const [partial, setPartial] = useState(null) // {agent_id, text}
  const [thinking, setThinking] = useState(null)
  const [doing, setDoing] = useState(null) // {agent_id, text}
  const [speaking, setSpeaking] = useState(null)
  const [panel, setPanel] = useState(null)
  const [captions, setCaptions] = useState(true)
  const [handsFree, setHandsFree] = useState(false)
  const [rec, setRec] = useState(false)
  const level = 0
  const [text, setText] = useState('')
  const [err, setErr] = useState('')
  const [secs, setSecs] = useState(0)
  const [ended, setEnded] = useState(null)
  const [mode, setMode] = useState('starting') // starting | live | classic
  const [muted, setMuted] = useState(true)
  const rtRef = useRef(null)
  const [caption, setCaption] = useState(null)
  const busy = useRef(false)
  const gen = useRef(0)
  const chain = useRef(Promise.resolve())
  const curAudio = useRef(null)
  const abortRef = useRef(null)
  const done = useRef(false)
  const scroller = useRef(null)


  useEffect(() => {
    let alive = true
    done.current = false
    api.get(`/meetings/${id}`).then(async (x) => {
      if (!alive) return
      setM(x); setMsgs(x.messages)
      if (x.status === 'ended') { setEnded(x); return }
      if (x.kind === 'admin_chat') { nav(`/team-chat?id=${id}`, { replace: true }); return }
      // live voice with OpenAI Realtime; fall back to the typed/turn-based room if it cannot connect
      const agentsIn = x.agents
      const rt = createRT({
        meetingId: id, agents: agentsIn, group: x.kind !== 'one_to_one',
        h: {
          onAdmin: async (t) => { await api.post(`/meetings/${id}/log`, { role: 'admin', text: t }); setMsgs((p) => [...p, { id: 'u' + Date.now(), role: 'admin', text: t }]) },
          onAgent: (aid, t, tools) => {
            setMsgs((p) => [...p, { id: 'a' + Date.now() + aid, role: 'agent', agent_id: aid, text: t, tools }])
            api.post(`/meetings/${id}/log`, { role: 'agent', agent_id: aid, text: t, tools }).catch(() => {})
            setTimeout(() => setCaption((c) => (c && c.text === t ? null : c)), 2500)
          },
          onPartial: (aid, t) => setPartial(aid ? { agent_id: aid, text: t } : null),
          onCaption: (aid, t) => setCaption(aid && t ? { agent_id: aid, text: t } : null),
          onSpeaking: setSpeaking, onThinking: setThinking, onRec: setRec, onErr: setErr,
          onDoing: (aid, name) => setDoing(aid ? { agent_id: aid, text: TOOL_TEXT[name] || 'working on it' } : null),
        },
      })
      try {
        await rt.start()
        if (!alive) { rt.close(); return }
        rtRef.current = rt; setMode('live')

      } catch (e) {
        rt.close()
        if (!alive) return
        setMode('classic'); setErr(`Live voice unavailable (${e.message || 'no microphone'}). Using the typed room.`)

      }
    }).catch((e) => setErr(e.message))
    return () => { alive = false; done.current = true; rtRef.current?.close(); rtRef.current = null; interrupt(); abortRef.current?.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])
  useEffect(() => { const t = setInterval(() => setSecs((s) => s + 1), 1000); return () => clearInterval(t) }, [])
  useEffect(() => { scroller.current?.scrollTo({ top: 1e9, behavior: 'smooth' }) }, [msgs, partial])

  // ---- agent voices (played one after another) ----
  const enqueueSpeech = (agentId, t) => {
    const my = gen.current
    const blobP = apiFetch(`/api/agents/${agentId}/speak`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: t }) })
      .then((r) => (r.ok ? r.blob() : null)).catch(() => null)
    chain.current = chain.current.then(async () => {
      if (my !== gen.current) return
      const blob = await blobP
      if (!blob || my !== gen.current) return
      await new Promise((res) => {
        const url = URL.createObjectURL(blob)
        const au = new Audio(url)
        curAudio.current = au
        setSpeaking(agentId); setCaption({ agent_id: agentId, text: t })
        au.onended = au.onerror = () => { URL.revokeObjectURL(url); res() }
        au.play().catch(res)
      })
      if (my === gen.current) { setSpeaking(null); setTimeout(() => setCaption((c) => (c && c.text === t ? null : c)), 1500) }
    })
  }
  function interrupt() {
    gen.current++
    chain.current = Promise.resolve()
    try { curAudio.current?.pause() } catch { /* ignore */ }
    setSpeaking(null)
  }

  // ---- talking to the agents ----
  const send = useCallback(async (t) => {
    t = (t || '').trim()
    if (!t || busy.current || done.current) return
    busy.current = true; setErr(''); abortRef.current = new AbortController()
    setMsgs((x) => [...x, { id: 'u' + Date.now(), role: 'admin', text: t }])
    try {
      await stream(`/meetings/${id}/message`, { headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: t }) }, (ev) => {
        if (ev.type === 'speaker') { setThinking(ev.agent_id); setPartial({ agent_id: ev.agent_id, text: '' }) }
        else if (ev.type === 'token') setPartial((p) => ({ agent_id: ev.agent_id, text: (p?.agent_id === ev.agent_id ? p.text : '') + ev.text }))
        else if (ev.type === 'tool_start') setDoing({ agent_id: ev.agent_id, text: TOOL_TEXT[ev.name] || 'working on it' })
        else if (ev.type === 'tool_result') setDoing(null)
        else if (ev.type === 'agent_pass') { setThinking(null); setPartial(null) }
        else if (ev.type === 'agent_done') {
          setThinking(null); setPartial(null); setDoing(null)
          setMsgs((x) => [...x, { id: 'a' + Date.now() + ev.agent_id, role: 'agent', agent_id: ev.agent_id, text: ev.text, tools: ev.tools }])
          enqueueSpeech(ev.agent_id, ev.text)
        } else if (ev.type === 'error') setErr(ev.message)
      }, abortRef.current.signal)
    } catch (e) { if (e.name !== 'AbortError') setErr(e.message) }
    setThinking(null); setPartial(null); setDoing(null)
    busy.current = false
    await chain.current

  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const stopListening = () => {
    rtRef.current?.setMuted(true)
    setMuted(true); setRec(false)
  }
  useEffect(() => {
    const hide = () => { if (document.hidden) stopListening() }
    window.addEventListener('blur', stopListening)
    document.addEventListener('visibilitychange', hide)
    return () => { window.removeEventListener('blur', stopListening); document.removeEventListener('visibilitychange', hide) }
  }, [])
  const beginTalk = () => { if (rtRef.current) { setMuted(false); rtRef.current.beginTalk() } }
  const endTalk = () => { rtRef.current?.endTalk(); setMuted(true) }
  const micClick = () => {
    if (!rtRef.current) return
    const next = !muted
    setMuted(next); rtRef.current.setMuted(next)
  }

  const leave = async () => {
    setMode('classic'); setMuted(true); setRec(false)
    done.current = true; rtRef.current?.close(); rtRef.current = null; interrupt(); abortRef.current?.abort()
    try { const x = await api.post(`/meetings/${id}/end`); setEnded(x) } catch (e) { setErr(e.message) }
  }

  if (!m) return <div className="meet"><p style={{ margin: 'auto', color: 'rgba(243,237,232,.6)' }}>{err || 'Joining…'}</p></div>
  const agents = m.agents
  const name = (aid) => agents.find((a) => a.id === aid)?.name || 'Agent'
  const mm = String(Math.floor(secs / 60)).padStart(2, '0') + ':' + String(secs % 60).padStart(2, '0')

  if (ended) {
    let minutes = {}
    try { minutes = JSON.parse(ended.summary || '{}') } catch { /* ignore */ }
    return (
      <div className="meet ended">
        <div className="minutes">
          <h1>{m.title} ended</h1>
          {minutes.summary && <p>{minutes.summary}</p>}
          {minutes.decisions?.length > 0 && (<><h3>Decisions</h3><ul>{minutes.decisions.map((d, i) => <li key={i}>{d}</li>)}</ul></>)}
          {minutes.action_items?.length > 0 && (<><h3>Action items</h3><ul>{minutes.action_items.map((d, i) => <li key={i}>{d}</li>)}</ul></>)}
          <h3>Transcript</h3>
          <div className="mtranscript">{(ended.messages || msgs).filter((x) => !hidden(x.text)).map((x) => <p key={x.id}><b>{x.role === 'admin' ? 'You' : name(x.agent_id)}:</b> {x.text}</p>)}</div>
          <button className="btn solid" onClick={() => nav('/')}>Back to the team</button>
        </div>
      </div>
    )
  }

  return (
    <div className="meet meet-polished">
      <header className="meet-topbar">
        <div className="meet-identity"><span className="meet-mark">D</span><span><b>{m.title}</b><small>{agents.map((a) => a.name).join(' · ')}</small></span></div>
        <div className="meet-top-actions"><span className="meet-live"><i /> {mode === 'starting' ? 'Connecting' : 'In meeting'}</span><span className="meet-duration">{mm}</span>
          <button className={`meet-top-btn ${panel === 'people' ? 'selected' : ''}`} onClick={() => setPanel(panel === 'people' ? null : 'people')} title="People"><Icon n="people" size={18} /><span>People</span></button>
          <button className={`meet-top-btn ${panel === 'chat' ? 'selected' : ''}`} onClick={() => setPanel(panel === 'chat' ? null : 'chat')} title="Meeting chat"><Icon n="chat" size={18} /><span>Chat</span></button>
        </div>
      </header>
      <div className="meet-main">
        <div className={`tiles n${agents.length + 1}`}>
          {agents.map((a) => (
            <div key={a.id} className={`tile ${speaking === a.id ? 'speaking' : ''}`}>
              <Avatar agent={a} size={agents.length > 2 ? 96 : 132} speaking={speaking === a.id} thinking={thinking === a.id && !doing} />
              {doing?.agent_id === a.id && <span className="doing">{a.name} is {doing.text}…</span>}
              {thinking === a.id && !doing && <span className="doing">{a.name} is thinking…</span>}
              <span className="nameplate">{a.name}{speaking === a.id && <i className="eq"><s /><s /><s /></i>}</span>
            </div>
          ))}
          <div className={`tile you ${rec ? 'speaking' : ''}`} style={rec ? { outlineColor: `rgba(138,180,248,${0.35 + level * 0.65})` } : {}}>
            <span className="avatar you-av" style={{ '--c': '#6b5d57', width: 84, height: 84, fontSize: 36 }}><b>Y</b></span>
            <span className="nameplate">You · {rec ? "Listening" : "Mic off"}{(mode === 'live' ? muted : !rec) && <svg width="14" height="14" viewBox="0 0 24 24" fill="#f28b82" style={{ marginLeft: 6 }}>{I.mic}</svg>}</span>
          </div>
        </div>

        {panel && (
          <aside className="side">
            <header className="meet-panel-head">
              <span><b>{panel === 'chat' ? 'Meeting chat' : 'People'}</b><small>{panel === 'chat' ? 'Messages stay with this meeting' : `${agents.length + 1} people here`}</small></span>
              <button className="x" onClick={() => setPanel(null)} aria-label="Close panel">✕</button>
            </header>
            {panel === 'people' ? (
              <ul className="ppl">
                <li><span className="avatar" style={{ '--c': '#6b5d57', width: 32, height: 32, fontSize: 14 }}><b>Y</b></span> You (host)</li>
                {agents.map((a) => <li key={a.id}><Avatar agent={a} size={32} /> {a.name} <small>{a.role}</small></li>)}
              </ul>
            ) : (
              <>
                <div className="msgs" ref={scroller}>
                  {!msgs.length && <p className="muted small">Take your time. Hold Talk when you’re ready, or type a message below.</p>}
                  {msgs.filter((x) => !hidden(x.text)).map((x) => (
                    <div key={x.id} className="m"><b>{x.role === 'admin' ? 'You' : name(x.agent_id)}</b><p>{x.text}</p>
                      {x.tools?.length > 0 && <small>{x.tools.map((t) => TOOL_TEXT[t.name] || t.name).join(' · ')}</small>}</div>
                  ))}
                  {partial?.text && <div className="m live"><b>{name(partial.agent_id)}</b><p>{partial.text}</p></div>}
                </div>
                <form className="msgbox" onSubmit={async (e) => {
                  e.preventDefault(); const t = text.trim(); if (!t || mode === 'starting' || busy.current) return
                  setText('')
                  if (rtRef.current) {
                    stopListening(); rtRef.current.interruptAll()
                    try {
                      await api.post(`/meetings/${id}/log`, { role: 'admin', text: t })
                      setMsgs((p) => [...p, { id: 'u' + Date.now(), role: 'admin', text: t }]); rtRef.current?.typed(t)
                    } catch (error) { setErr(error.message); setText(t) }
                  } else { interrupt(); send(t) }
                }}>
                  <input aria-label="Message the meeting" value={text} onChange={(e) => setText(e.target.value)} placeholder="Send a message to everyone" />
                  <button type="submit" className="btn" disabled={!text.trim() || mode === 'starting'}>Send</button>
                </form>
              </>
            )}
          </aside>
        )}
      </div>

      {captions && caption && (
        <div className="captions"><b>{name(caption.agent_id)}</b> {caption.text}</div>
      )}
      {handsFree && <div className="meet-listening-note">{muted ? "Microphone muted" : "Microphone on — nearby voices may be heard. Mute for side conversations."}</div>}
      {err && <div className="meet-err" onClick={() => setErr('')}>{err}</div>}

      <footer className="bar meet-controls">
        <div className="mid">
          {mode === 'live' && <>
            {!handsFree ? <button className={`btn ${rec ? 'solid' : ''}`} style={{ touchAction: 'none', userSelect: 'none' }}
              onPointerDown={(e) => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); beginTalk() }}
              onPointerUp={endTalk} onBlur={stopListening} onPointerCancel={stopListening} onLostPointerCapture={() => { if (rec) endTalk() }}
              onKeyDown={(e) => { if ([' ', 'Enter'].includes(e.key)) { e.preventDefault(); if (!e.repeat) beginTalk() } }}
              onKeyUp={(e) => { if ([' ', 'Enter'].includes(e.key)) { e.preventDefault(); endTalk() } }}
              aria-label="Hold to talk; release to send">{rec ? 'Listening… release to send' : 'Hold to talk'}</button>
              : <button className={`cbtn ${muted ? 'off' : 'on'}`} onClick={micClick} aria-label={muted ? 'Unmute microphone' : 'Mute microphone'} title={muted ? 'Unmute microphone' : 'Mute microphone'}><Icon n="mic" /></button>}
            <label className="small"><input type="checkbox" checked={handsFree} onChange={(e) => { const value = e.target.checked; setHandsFree(value); setMuted(true); rtRef.current?.setHandsFree(value) }} /> Hands-free</label>
          </>}
          <button className="cbtn" onClick={() => { rtRef.current?.interruptAll(); interrupt(); abortRef.current?.abort() }} title="Stop current reply" aria-label="Stop speaking"><span className="meet-stop-icon" /></button>
          <button className={`cbtn ${captions ? 'on' : ''}`} onClick={() => setCaptions(!captions)} title="Captions"><Icon n="cc" /></button>
          <button className="cbtn end" onClick={leave} title="Leave meeting" aria-label="Leave meeting"><Icon n="end" /></button>
        </div>
        <div className="right"><span>{mode === 'live' ? (rec ? 'Listening · release to send' : 'Mic off') : mode === 'starting' ? 'Joining…' : 'Text mode'}</span></div>
      </footer>
    </div>
  )
}
