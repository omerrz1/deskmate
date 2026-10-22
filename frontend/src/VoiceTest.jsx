import { useEffect, useRef, useState } from 'react'
import { api, apiFetch } from './api'

export default function VoiceTest({ voice = 'marin' }) {
  const [state, setState] = useState('idle')
  const [error, setError] = useState('')
  const [text, setText] = useState('Hello! Thanks for calling. How can I help you today?')
  const session = useRef(null)
  const stop = () => {
    const s = session.current
    session.current = null
    s?.pc?.close()
    s?.mic?.getTracks().forEach(t => t.stop())
    s?.audio?.pause()
    if (s?.url) URL.revokeObjectURL(s.url)
    setState('idle')
  }
  useEffect(() => { stop(); return stop }, [voice]) // eslint-disable-line
  const preview = async () => {
    stop(); setError(''); setState('loading')
    const s = {}; session.current = s
    try {
      const r = await apiFetch('/api/voices/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice, text }) })
      if (!r.ok) throw new Error('Voice preview failed. Check your voice connection in Settings.')
      const blob = await r.blob()
      if (session.current !== s) return
      s.url = URL.createObjectURL(blob); s.audio = new Audio(s.url)
      s.audio.onended = stop
      await s.audio.play(); setState('playing')
    } catch (e) { if (session.current === s) { stop(); setError(e.message) } }
  }
  const start = async () => {
    stop(); setError(''); setState('connecting')
    const s = {}; session.current = s
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone testing requires HTTPS or localhost.')
      s.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      if (session.current !== s) { s.mic.getTracks().forEach(t => t.stop()); return }
      const token = await api.post('/voices/test', { voice })
      if (session.current !== s) return
      s.pc = new RTCPeerConnection(); s.audio = new Audio(); s.audio.autoplay = true
      s.pc.ontrack = e => { s.audio.srcObject = e.streams[0]; s.audio.play().catch(() => setError('Allow audio playback in your browser.')) }
      s.mic.getTracks().forEach(t => s.pc.addTrack(t, s.mic))
      s.pc.onconnectionstatechange = () => {
        if (session.current !== s) return
        if (s.pc.connectionState === 'connected') setState('live')
        if (s.pc.connectionState === 'failed') { stop(); setError('The voice connection failed. Please retry.') }
      }
      s.dc = s.pc.createDataChannel('oai-events')
      s.dc.onmessage = e => {
        try { const event = JSON.parse(e.data); if (event.type === 'error') setError(event.error?.message || 'Voice error') } catch { /* ignore non-JSON */ }
      }
      const offer = await s.pc.createOffer(); await s.pc.setLocalDescription(offer)
      const r = await fetch('https://api.openai.com/v1/realtime/calls', { method: 'POST', headers: { Authorization: `Bearer ${token.secret}`, 'Content-Type': 'application/sdp' }, body: offer.sdp })
      if (!r.ok) throw new Error('Could not connect the live voice test.')
      const sdp = await r.text()
      if (session.current === s) await s.pc.setRemoteDescription({ type: 'answer', sdp })
    } catch (e) { if (session.current === s) { stop(); setError(e.message) } }
  }
  return <div className="section form-grid">
    <label className="f">Preview {voice}<textarea rows="2" value={text} onChange={e => setText(e.target.value)} maxLength={2000} /></label>
    <div className="actions">
      <button className="btn" disabled={state !== 'idle' || !text.trim()} onClick={preview}>Listen</button>
      <button className="btn" disabled={state !== 'idle'} onClick={start}>Talk to this voice</button>
      {state !== 'idle' && <button className="btn" onClick={stop}>Stop</button>}
    </div>
    <p className="small muted" role="status">{state === 'live' ? 'Microphone on — speak naturally to test the voice.' : state === 'connecting' ? 'Connecting microphone…' : state === 'loading' ? 'Preparing preview…' : 'Tests the selected voice without saving it. Live testing uses your voice API connection.'}</p>
    {error && <p role="alert">{error}</p>}
  </div>
}
