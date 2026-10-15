import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, apiFetch, stream, pulse } from './api'

const SUGGEST = [
  'How are we doing today?',
  'Call all overdue customers',
  'Who needs a callback?',
]
const TOOL_LABEL = {
  get_dashboard_stats: 'Reading dashboard', get_segments: 'Scanning segments', search_contacts: 'Searching customers',
  create_campaign: 'Creating campaign', start_campaign: 'Starting campaign',
  stop_campaign: 'Stopping campaign', list_campaigns: 'Listing campaigns', call_contact: 'Queuing call',
  list_calls: 'Reviewing calls', get_call: 'Opening call', get_client_report: 'Writing client report', update_contact: 'Updating customer',
}

function Rich({ text }) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith('**') ? <b key={i}>{p.slice(2, -2)}</b> : <span key={i}>{p}</span>))
}

export default function ChatDock() {
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [rec, setRec] = useState(false)
  const [levels, setLevels] = useState([0, 0, 0, 0, 0])
  const [speaking, setSpeaking] = useState(false)
  const [err, setErr] = useState('')
  const scroller = useRef(null)
  const media = useRef({})
  const audioRef = useRef(null)
  const abort = useRef(null)

  useEffect(() => { scroller.current?.scrollTo({ top: 1e9, behavior: 'smooth' }) }, [msgs, open])
  useEffect(() => { pulse(busy ? 0.55 : speaking ? 0.8 : rec ? 0.4 : 0) }, [busy, speaking, rec])

  const patchLast = (fn) => setMsgs((m) => { const c = m.slice(); c[c.length - 1] = fn({ ...c[c.length - 1] }); return c })

  async function send(text, fromVoice = false) {
    text = (text ?? input).trim()
    if (!text || busy) return
    setErr(''); setInput(''); setOpen(true); setBusy(true)
    const history = [...msgs, { role: 'user', content: text }]
    setMsgs([...history, { role: 'assistant', content: '', tools: [] }])
    abort.current = new AbortController()
    let final = ''
    try {
      await stream('/chat', {
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ messages: history.map(({ role, content }) => ({ role, content })) }),
      }, (ev) => {
        if (ev.type === 'token') { final += ev.text; patchLast((l) => ({ ...l, content: l.content + ev.text })) }
        if (ev.type === 'tool_start') patchLast((l) => ({ ...l, tools: [...(l.tools || []), { name: ev.name, running: true }] }))
        if (ev.type === 'tool_result') patchLast((l) => ({ ...l, tools: (l.tools || []).map((t) => (t.name === ev.name && t.running ? { ...t, running: false, link: ev.link, ok: ev.ok } : t)) }))
        if (ev.type === 'error') setErr(ev.message)
      }, abort.current.signal)
      if (fromVoice && final) speak(final)
    } catch (e) {
      if (e.name !== 'AbortError') setErr(e.message)
    } finally { setBusy(false) }
  }

  async function speak(text) {
    try {
      const r = await apiFetch('/tts', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: text.slice(0, 700) }) })
      if (!r.ok) return
      const url = URL.createObjectURL(await r.blob())
      audioRef.current?.pause()
      const a = new Audio(url)
      audioRef.current = a
      a.onplay = () => setSpeaking(true)
      a.onended = a.onpause = () => setSpeaking(false)
      a.play()
    } catch { /* ignore */ }
  }

  async function toggleMic() {
    if (rec) { media.current.recorder?.stop(); return }
    audioRef.current?.pause()
    try {
      const stream_ = await navigator.mediaDevices.getUserMedia({ audio: true })
      const ctx = new AudioContext()
      const an = ctx.createAnalyser(); an.fftSize = 64
      ctx.createMediaStreamSource(stream_).connect(an)
      const buf = new Uint8Array(an.frequencyBinCount)
      const recorder = new MediaRecorder(stream_)
      const chunks = []
      let raf
      const tick = () => {
        an.getByteFrequencyData(buf)
        setLevels([2, 6, 10, 14, 18].map((i) => buf[i] / 255))
        raf = requestAnimationFrame(tick)
      }
      tick()
      recorder.ondataavailable = (e) => chunks.push(e.data)
      recorder.onstop = async () => {
        cancelAnimationFrame(raf); stream_.getTracks().forEach((t) => t.stop()); ctx.close()
        setRec(false); setLevels([0, 0, 0, 0, 0])
        const fd = new FormData()
        fd.append('audio', new Blob(chunks, { type: recorder.mimeType }), 'speech.webm')
        try {
          const { text } = await api.form('/transcribe', fd)
          if (text) send(text, true)
        } catch (e) { setErr(e.message) }
      }
      media.current = { recorder }
      recorder.start()
      setRec(true); setOpen(true)
    } catch (e) { setErr('Microphone unavailable: ' + e.message) }
  }

  return (
    <div className={`dock ${open ? 'open' : ''}`}>
      {open && (
        <div className="dock-panel">
          <div className="dock-head">
            <span><i className={`dot ${busy ? 'live' : ''}`} /> Atlas · admin copilot</span>
            <div>
              {msgs.length > 0 && <button className="link" onClick={() => { abort.current?.abort(); setMsgs([]) }}>Clear</button>}
              <button className="link" onClick={() => setOpen(false)}>Hide</button>
            </div>
          </div>
          <div className="dock-msgs" ref={scroller}>
            {msgs.length === 0 && <p className="muted">Ask me to inspect data, launch campaigns, review calls or write client reports. You can also talk to me with the mic.</p>}
            {msgs.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                {(m.tools || []).map((t, j) => (
                  <div key={j} className={`tool ${t.running ? 'run' : ''}`}>
                    <i className="dot" /> {TOOL_LABEL[t.name] || t.name}
                    {!t.running && t.link && <Link to={t.link} onClick={() => setOpen(false)}>open ↗</Link>}
                  </div>
                ))}
                {m.content ? <p><Rich text={m.content} /></p> : m.role === 'assistant' && busy && i === msgs.length - 1 && !(m.tools || []).length ? <p className="typing"><i /><i /><i /></p> : null}
              </div>
            ))}
            {err && <div className="msg error">{err}</div>}
          </div>
        </div>
      )}
      {!open && msgs.length === 0 && !input && (
        <div className="suggest">
          {SUGGEST.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      <form className={`dock-bar ${rec ? 'rec' : ''}`} onSubmit={(e) => { e.preventDefault(); send() }}>
        <button type="button" className="orb-btn" onClick={() => setOpen((o) => !o)} title="Toggle conversation">
          <span className={`orb ${busy || speaking ? 'active' : ''}`} />
        </button>
        {rec ? (
          <div className="wave">{levels.map((l, i) => <i key={i} style={{ height: `${8 + l * 30}px` }} />)}<span>Listening… tap the mic to send</span></div>
        ) : (
          <input value={input} onChange={(e) => setInput(e.target.value)} onFocus={() => msgs.length && setOpen(true)}
            placeholder="Ask or tell the agent what to do…" />
        )}
        <button type="button" className={`icon-btn ${rec ? 'on' : ''}`} onClick={toggleMic} title="Voice chat with the agent">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
        </button>
        <button type="submit" className="icon-btn send" disabled={busy || !input.trim()} title="Send">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
        </button>
      </form>
    </div>
  )
}
