import { useEffect, useRef, useState } from 'react'
import { api, usePoll } from './api'

const QUICK = ['Yes', 'No', 'Tell them I will call back today', 'Ask them to email us']

/** Red bar across the top whenever an employee has a customer on hold and needs an answer from you. */
export default function LiveHelpBanner() {
  const [qs, reload] = usePoll('/live-questions', 1500)
  const [text, setText] = useState({})
  const [remember, setRemember] = useState(true)
  const [rec, setRec] = useState(false)
  const [err, setErr] = useState('')
  const media = useRef(null)
  const seen = useRef(new Set())
  const title0 = useRef(document.title)
  const list = qs || []

  useEffect(() => {  // a sound and a notification once per new question, and a flashing tab title while any are open
    for (const q of list) {
      if (seen.current.has(q.id)) continue
      seen.current.add(q.id)
      try {
        const a = new (window.AudioContext || window.webkitAudioContext)()
        for (const [i, f] of [880, 660, 880].entries()) {
          const o = a.createOscillator(), g = a.createGain()
          o.frequency.value = f; g.gain.value = 0.08; o.connect(g); g.connect(a.destination)
          o.start(a.currentTime + i * 0.22); o.stop(a.currentTime + i * 0.22 + 0.16)
        }
        setTimeout(() => a.close(), 1200)
      } catch { /* sound is a nicety */ }
      try { if ('Notification' in window && Notification.permission === 'granted') new Notification(`${q.agent_name} has a customer on hold`, { body: q.question }) } catch { /* ignore */ }
    }
    if (!list.length) { document.title = title0.current; return }
    let on = false
    const t = setInterval(() => { on = !on; document.title = on ? '🔴 Customer on hold' : title0.current }, 900)
    return () => { clearInterval(t); document.title = title0.current }
  }, [list.length]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (list.length && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {}) }, [list.length > 0]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!list.length) return null
  const answer = async (q, a) => {
    if (!a.trim()) return
    setErr('')
    try { await api.post(`/live-questions/${q.id}/answer`, { answer: a, remember }); setText((t) => ({ ...t, [q.id]: '' })); reload() } catch (e) { setErr(e.message); reload() }
  }
  const speak = async (q) => {
    if (rec) { media.current?.stop(); return }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = new MediaRecorder(s), chunks = []
      r.ondataavailable = (e) => chunks.push(e.data)
      r.onstop = async () => {
        s.getTracks().forEach((t) => t.stop()); setRec(false)
        const fd = new FormData(); fd.append('audio', new Blob(chunks, { type: r.mimeType }), 'speech.webm')
        try { const { text: t } = await api.form('/transcribe', fd); if (t) setText((x) => ({ ...x, [q.id]: ((x[q.id] || '') + ' ' + t).trim() })) } catch (e) { setErr(e.message) }
      }
      media.current = r; r.start(); setRec(true)
    } catch (e) { setErr('Microphone unavailable: ' + e.message) }
  }

  return (
    <div className="livehelp" role="alert">
      {list.map((q) => (
        <div key={q.id} className="lh-card">
          <div className="lh-top"><b>🔴 {q.agent_name || 'Your assistant'} has {q.contact_name || 'a customer'} on hold</b><span className="lh-wait">{q.waiting}s</span></div>
          <p className="lh-q">{q.question}</p>
          {q.context && <p className="small lh-ctx">{q.context}</p>}
          <div className="lh-quick">{QUICK.map((a) => <button key={a} className="btn small" onClick={() => answer(q, a)}>{a}</button>)}</div>
          <div className="lh-row">
            <input autoFocus value={text[q.id] || ''} placeholder="Type your answer" onChange={(e) => setText({ ...text, [q.id]: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && answer(q, text[q.id] || '')} />
            <button className={`btn small ${rec ? 'solid' : ''}`} onClick={() => speak(q)}>{rec ? 'Stop' : '🎤 Say it'}</button>
            <button className="btn small solid" disabled={!(text[q.id] || '').trim()} onClick={() => answer(q, text[q.id] || '')}>Send</button>
            <button className="link" onClick={async () => { await api.post(`/live-questions/${q.id}/decline`); reload() }}>Can't answer now</button>
          </div>
          <label className="check small"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Remember this answer for future callers</label>
        </div>
      ))}
      {err && <p className="small lh-err">{err}</p>}
    </div>
  )
}
