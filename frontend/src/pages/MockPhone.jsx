import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Conversation } from '@elevenlabs/client'
import { api, apiFetch, stream, pulse, dur } from '../api'


// ---- sentence splitting (mirrors backend llm.split_sentences; handles । ؟ ۔ 。 etc.) ----
const SENT = /(.+?(?:[!?؟۔।॥。！？]+["')\]]*|\.+["')\]]*\s+|\n))/gs
const CLAUSE = /^(.{14,}?[,،，、;؛:]\s*)/s
function splitSentences(buf, eager) {
  const out = []
  let pos = 0, m
  SENT.lastIndex = 0
  while ((m = SENT.exec(buf))) { out.push(m[1].trim()); pos = SENT.lastIndex }
  let rest = buf.slice(pos)
  if (eager && !out.length) { const c = CLAUSE.exec(rest); if (c) { out.push(c[1].trim()); rest = rest.slice(c[0].length) } }
  return [out.filter(Boolean), rest]
}

/** Speaks text with a native-voice TTS (via backend), gapless and interruptible. */
class Speaker {
  constructor(ac, dest, language, onBusy) {
    this.ac = ac; this.dest = dest; this.language = language; this.onBusy = onBusy
    this.out = ac.createGain(); this.out.connect(ac.destination)
    this.gen = 0; this.chain = Promise.resolve(); this.next = 0; this.active = 0; this.sources = new Set()
  }
  get busy() { return this.active > 0 }
  setMuted(m) { this.out.gain.value = m ? 0 : 1 }
  enqueue(text) {
    const gen = this.gen
    this.active++; this.onBusy(true)
    const p = apiFetch('/tts', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text, language: this.language }) })
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error('tts'))))
      .then((b) => this.ac.decodeAudioData(b))
    const finish = () => { if (gen === this.gen) { this.active = Math.max(0, this.active - 1); if (!this.active) this.onBusy(false) } }
    this.chain = this.chain.then(async () => {
      let buf
      try { buf = await p } catch { finish(); return }
      if (gen !== this.gen) return
      const src = this.ac.createBufferSource()
      src.buffer = buf
      src.connect(this.out); src.connect(this.dest)
      const at = Math.max(this.ac.currentTime + 0.02, this.next)
      src.start(at); this.next = at + buf.duration
      this.sources.add(src)
      src.onended = () => { this.sources.delete(src); finish() }
    })
  }
  stop() {
    this.gen++
    this.sources.forEach((s) => { try { s.stop() } catch { /* ignore */ } })
    this.sources.clear(); this.next = 0; this.active = 0; this.chain = Promise.resolve()
    this.onBusy(false)
  }
}

/**
 * TEMPORARY simulator page. It plays the role of the customer's phone:
 * it polls the backend for a ringing call, lets you answer, talk (mic or keyboard) and hang up.
 * When Twilio is wired in, the real phone network replaces this page entirely.
 */
export default function MockPhone({ website = false }) {
  const [phase, setPhase] = useState('idle') // idle | ringing | connecting | incall | ended
  const [call, setCall] = useState(null)
  const [waiting, setWaiting] = useState(0)
  const [lines, setLines] = useState([])
  const [text, setText] = useState('')
  const [state, setState] = useState('listening') // listening | speaking | thinking
  const [micOn, setMicOn] = useState(true)
  const [muted, setMuted] = useState(false)
  const [agentName, setAgentName] = useState('Maya')
  const [voiceNote, setVoiceNote] = useState('')
  const [err, setErr] = useState('')
  const [secs, setSecs] = useState(0)
  const [now, setNow] = useState(new Date())
  const [contacts, setContacts] = useState([])
  const [pick, setPick] = useState({ id: '', objective: 'Check in with the customer and see if they need anything.' })
  const [toast, setToast] = useState('')

  const rt = useRef({})          // live connection objects
  const linesRef = useRef([])
  const callRef = useRef(null)
  const hold = useRef(null)
  const [onHold, setOnHold] = useState(false)
  const prefetch = useRef(null)
  const dbg = (event, detail = '') => { const c = callRef.current; if (c) api.post(`/phone/${c.id}/debug`, { event, detail: String(detail) }).catch(() => {}) }
  const phaseRef = useRef(phase)
  const scroller = useRef(null)
  phaseRef.current = phase

  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t) }, [])
  useEffect(() => { api.get('/contacts?limit=200').then((r) => { setContacts(r.items); if (r.items[0]) setPick((p) => ({ ...p, id: r.items[0].id })) }).catch(() => {}) }, [])

  // poll for ringing calls while idle
  useEffect(() => {
    if (phase !== 'idle' && phase !== 'ringing') return
    let alive = true
    const tick = async () => {
      try {
        const r = await api.get('/phone/poll')
        if (!alive || callRef.current) return  // never touch a call this page is already running
        setWaiting(r.waiting)
        if (r.call && r.call.status === 'ringing') {
          setCall(r.call); setPhase('ringing')
          if (prefetch.current?.id !== r.call.id) prefetch.current = { id: r.call.id, at: Date.now(), p: api.post(`/phone/${r.call.id}/realtime`).catch(() => null) } // set up the voice session while it rings so answering is instant
        }
        else if (r.call && r.call.status === 'in_progress') {
          // only clean up calls that are clearly left over from a closed tab
          if (r.call.answered_at && Date.now() / 1000 - r.call.answered_at > 90) await api.post(`/phone/${r.call.id}/hangup`)
        }
        else if (!r.call) { setCall(null); setPhase('idle') }
      } catch { /* backend down */ }
    }
    tick()
    const t = setInterval(tick, 1500)
    return () => { alive = false; clearInterval(t) }
  }, [phase])

  useEffect(() => {
    if (phase !== 'incall') return
    const t = setInterval(() => setSecs((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [phase])
  useEffect(() => { scroller.current?.scrollTo({ top: 1e9, behavior: 'smooth' }) }, [lines])
  useEffect(() => { pulse(phase === 'incall' ? (state === 'speaking' ? 0.9 : state === 'thinking' ? 0.5 : 0.15) : phase === 'ringing' ? 0.6 : 0) }, [state, phase])
  useEffect(() => () => teardown(), [])

  // ---- transcript bookkeeping ----
  const tNow = () => (rt.current.start ? (Date.now() - rt.current.start) / 1000 : 0)
  function upsert(id, role, patch) {
    const arr = linesRef.current
    let l = arr.find((x) => x.id === id)
    if (!l) { l = { id, role, text: '', t: Math.round(tNow() * 10) / 10 }; arr.push(l) }
    Object.assign(l, patch)
    setLines([...arr])
  }
  function save() {
    const c = callRef.current
    if (!c) return
    api.post(`/phone/${c.id}/transcript`, { lines: linesRef.current.filter((l) => l.text).map(({ role, text, t }) => ({ role, text, t })) }).catch(() => {})
  }

  // run fn once the model has finished its current response (it rejects new ones while talking)
  function whenIdle(fn) {
    const r = rt.current
    if (!r.active) fn()
    else (r.afterDone = r.afterDone || []).push(fn)
  }

  // soft hold music while the agent checks with the admin
  function startHold() {
    if (hold.current) return
    const own = !rt.current.ac
    const ac = rt.current.ac || new AudioContext()
    const g = ac.createGain(); g.gain.value = 0.05; g.connect(ac.destination)
    const notes = [392, 523.25, 659.25, 523.25, 440, 587.33, 659.25, 587.33]
    let i = 0
    const tick = () => {
      const o = ac.createOscillator(); o.type = 'sine'; o.frequency.value = notes[i++ % notes.length]
      const e = ac.createGain(); e.gain.setValueAtTime(0, ac.currentTime); e.gain.linearRampToValueAtTime(1, ac.currentTime + 0.06); e.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 1.3)
      o.connect(e); e.connect(g); o.start(); o.stop(ac.currentTime + 1.4)
    }
    tick()
    hold.current = { timer: setInterval(tick, 800), g, own, ac }
    setOnHold(true)
  }
  function stopHold() {
    const h = hold.current
    if (!h) return
    clearInterval(h.timer); try { h.g.disconnect() } catch { /* ignore */ }
    if (h.own) { try { h.ac.close() } catch { /* ignore */ } }
    hold.current = null; setOnHold(false)
  }

  // tool calls from the model (deduped: the same call can arrive via two events)
  function handleCall(name, callId, argStr) {
    const r = rt.current
    r.handled = r.handled || new Set()
    if (!callId || r.handled.has(callId)) return
    r.handled.add(callId)
    console.debug('[rt] tool call', name, argStr)
    const out = (text) => whenIdle(() => {
      const dc = rt.current.dc
      if (!dc || dc.readyState !== 'open') return
      dc.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'function_call_output', call_id: callId, output: text } }))
      dc.send(JSON.stringify({ type: 'response.create' }))
    })
    if (name === 'lookup_info') {
      let q = '', category = null
      try { const a = JSON.parse(argStr || '{}'); q = a.query || ''; category = a.category || null } catch { /* ignore */ }
      api.post('/knowledge/search', { kb_id: r.institution || 0, query: q, category, call_id: callRef.current?.id })
        .then((x) => x.results.join('\n\n')).catch(() => 'No information found. Say a colleague will confirm.').then(out)
    }
    if (name !== 'lookup_info' && name !== 'end_call') {
      let args = {}
      try { args = JSON.parse(argStr || '{}') } catch { /* ignore */ }
      if (name === 'ask_admin') startHold()
      api.post(`/phone/${callRef.current?.id}/tool`, { name, args }).then((x) => x.result).catch(() => 'That did not work. Carry on naturally and say a colleague will follow up.').then((x) => { if (name === 'ask_admin') stopHold(); return x }).then(out)
      dbg('tool', `${name} ${argStr || ''}`)
    }
    if (name === 'end_call') {
      if (!linesRef.current.some((l) => l.role === 'customer' && l.text) && tNow() < 45) {
        dbg('end_call blocked', 'customer has not spoken yet')
        out('Do not end the call yet: the customer has not spoken. Say "Hello? Can you hear me?" and wait for them.')
        return
      }
      dbg('end_call', 'agent finished')
      r.pendingEnd = true
      if (r.speaker && !r.speaker.busy) setTimeout(() => hangup(), 900)
      dc_ok(r) && r.dc.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'function_call_output', call_id: callId, output: 'ok' } }))
      setTimeout(() => { if (rt.current.pendingEnd) hangup() }, 6000) // safety net
    }
  }
  const dc_ok = (r) => r.dc && r.dc.readyState === 'open'

  // ---- realtime events from the model ----
  function onEvent(ev) {
    if (/function|output_item.done|error/.test(ev.type)) console.debug('[rt]', ev.type, ev.name || ev.item?.type || '')
    switch (ev.type) {
      case 'conversation.item.added':
        if (ev.item?.role === 'user') upsert(ev.item.id, 'customer', {})
        if (ev.item?.role === 'assistant') upsert(ev.item.id, 'agent', {})
        break
      case 'response.created':
        rt.current.live = rt.current.live || new Set(); rt.current.live.add(ev.response?.id)
        rt.current.active = (rt.current.active || 0) + 1
        break
      case 'response.done': {
        const r = rt.current
        r.active = Math.max(0, (r.active || 1) - 1)
        if (!r.active && r.afterDone?.length) { const fns = r.afterDone.splice(0); fns.forEach((f) => f()) }
        break
      }
      case 'input_audio_buffer.speech_started':
        setState('listening')
        if (rt.current.speaker) { rt.current.speaker.stop(); rt.current.dead = new Set([...(rt.current.live || [])]) } // customer interrupts: stop talking, ignore the rest of that answer
        break
      case 'response.output_text.delta':
      case 'response.text.delta': {
        const r = rt.current
        if (r.dead?.has(ev.response_id)) break
        const b = (r.buf = r.buf || {})
        const st = (b[ev.item_id] = b[ev.item_id] || { text: '', pending: '', n: 0 })
        st.text += ev.delta; st.pending += ev.delta
        upsert(ev.item_id, 'agent', { text: st.text })
        const [sents, rest] = splitSentences(st.pending, st.n === 0)
        st.pending = rest
        sents.forEach((x) => { st.n++; r.speaker?.enqueue(x) })
        break
      }
      case 'response.output_text.done':
      case 'response.text.done': {
        const r = rt.current
        if (r.dead?.has(ev.response_id)) break
        const st = r.buf?.[ev.item_id]
        if (st?.pending.trim()) { r.speaker?.enqueue(st.pending.trim()); st.pending = '' }
        upsert(ev.item_id, 'agent', { text: (ev.text || st?.text || '').trim() }); save()
        break
      }
      case 'input_audio_buffer.speech_stopped': setState('thinking'); break
      case 'conversation.item.input_audio_transcription.completed':
        upsert(ev.item_id, 'customer', { text: (ev.transcript || '').trim() }); save(); break
      case 'response.output_audio_transcript.delta':
      case 'response.audio_transcript.delta':
        upsert(ev.item_id, 'agent', { text: (linesRef.current.find((x) => x.id === ev.item_id)?.text || '') + ev.delta }); break
      case 'response.output_audio_transcript.done':
      case 'response.audio_transcript.done':
        upsert(ev.item_id, 'agent', { text: (ev.transcript || '').trim() }); save(); break
      case 'output_audio_buffer.started': setState('speaking'); break
      case 'output_audio_buffer.stopped':
        setState('listening')
        if (rt.current.pendingEnd) setTimeout(() => hangup(), 700)
        break
      case 'response.function_call_arguments.done':
        handleCall(ev.name, ev.call_id, ev.arguments); break
      case 'response.output_item.done':
        if (ev.item?.type === 'function_call') handleCall(ev.item.name, ev.item.call_id, ev.item.arguments)
        break
      case 'error':
        dbg('model error', ev.error?.message || '')
        if (!/active response|Cancellation failed|no active response/i.test(ev.error?.message || '')) setErr(ev.error?.message || 'Voice connection error')
        break
      default: break
    }
  }

  // ---- ElevenLabs Agents engine: native voice + their conversation loop; transcript/recording are synced back at hangup ----
  async function connectEleven(c, sess) {
    rt.current = { eleven: true, start: Date.now(), institution: sess.kb_id }
    let n = 0, heard = false
    let settle
    const started = new Promise((res, rej) => { settle = { res, rej } })
    started.catch(() => {})
    const conv = await Conversation.startSession({
      signedUrl: sess.signed_url,
      overrides: { agent: { prompt: { prompt: sess.prompt }, firstMessage: sess.first_message, language: sess.language_code }, tts: { voiceId: sess.voice_id, ...(sess.tuning || {}), ...(sess.tuning?.stability != null ? { similarityBoost: 0.75 } : {}) } },
      clientTools: {
        ...Object.fromEntries((sess.tool_names || []).map((name) => [name, async (args) => {
          dbg('tool', `${name} ${JSON.stringify(args || {})}`)
          if (name === 'ask_admin') startHold()
          try { return (await api.post(`/phone/${c.id}/tool`, { name, args: args || {} })).result } catch { return 'That did not work. Carry on and say a colleague will follow up.' } finally { if (name === 'ask_admin') stopHold() }
        }])),
        lookup_info: async ({ query, category }) => {
          try { const r = await api.post('/knowledge/search', { kb_id: sess.kb_id || 0, query, category: category || null, call_id: c.id }); return r.results.join('\n\n') }
          catch { return 'No information found. Say a colleague will confirm.' }
        },
      },
      onMessage: ({ source, message }) => {
        if (!message) return
        heard = true; settle.res()
        upsert(`el-${n++}`, source === 'user' ? 'customer' : 'agent', { text: String(message).replace(/\s*\[[a-z ]{3,24}\]\s*/g, ' ').trim() }); save()
      },
      onModeChange: ({ mode }) => { if (mode === 'speaking') { heard = true; settle.res() } setState(mode === 'speaking' ? 'speaking' : 'listening') },
      onDisconnect: (d) => { console.debug('[el] disconnect', JSON.stringify(d)); if (!heard) settle.rej(new Error(d?.reason || d?.message || 'ElevenLabs closed the call before the agent spoke')); else if (rt.current.ready && !callRef.current?.__ending) hangup() },
      onError: (m) => setErr(typeof m === 'string' ? m : (m?.message || 'Voice error')),
    })
    rt.current.conv = conv
    rt.current.conversationId = conv.getId()
    await Promise.race([started, new Promise((_, rej) => setTimeout(() => rej(new Error('ElevenLabs did not answer in time')), 12000))])
    rt.current.ready = true
  }

  async function connect(c, fallback = false) {
    // 1. short-lived key + instructions for this customer
    const pre = !fallback && prefetch.current?.id === c.id && Date.now() - prefetch.current.at < 90000 ? await prefetch.current.p : null
    prefetch.current = null
    const sess = pre || await api.post(`/phone/${c.id}/realtime${fallback ? '?fallback=1' : ''}`)
    setAgentName(sess.agent_name || 'Maya'); setVoiceNote(sess.voice_note || '')
    if (sess.engine === 'elevenlabs') {
      try { return await connectEleven(c, sess) } catch (e) {
        dbg('elevenlabs failed', e?.message || String(e))
        teardown(); setVoiceNote('ElevenLabs unavailable, using the OpenAI voice')
        return connect(c, true)
      }
    }
    // 2. microphone (silent fallback so the tester can still type)
    const ac = new AudioContext()
    let mic
    try { mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }) }
    catch { setErr('Microphone blocked: you can still type your replies.'); setMicOn(false); const d = ac.createMediaStreamDestination(); mic = d.stream }
    const dest = ac.createMediaStreamDestination() // used only to mix the browser-voice playback; nothing is recorded
    ac.createMediaStreamSource(mic).connect(dest)
    rt.current = {}
    const pc = new RTCPeerConnection()
    const audioEl = new Audio(); audioEl.autoplay = true
    pc.ontrack = (e) => { audioEl.srcObject = e.streams[0]; ac.createMediaStreamSource(e.streams[0]).connect(dest) }
    mic.getAudioTracks().forEach((t) => pc.addTrack(t, mic))
    if (sess.external) {
      rt.current.speaker = new Speaker(ac, dest, sess.language, (busy) => {
        setState(busy ? 'speaking' : 'listening')
        if (!busy && rt.current.pendingEnd) setTimeout(() => hangup(), 700)
      })
    }
    let lostTimer = null
    pc.onconnectionstatechange = () => {
      dbg('connection', pc.connectionState)
      if (pc.connectionState === 'connected') clearTimeout(lostTimer)
      if (pc.connectionState === 'failed') { setErr('The connection dropped.'); hangup() }
      if (pc.connectionState === 'disconnected') { clearTimeout(lostTimer); lostTimer = setTimeout(() => { if (pc.connectionState !== 'connected') { setErr('The connection dropped.'); hangup() } }, 7000) }
    }
    const dc = pc.createDataChannel('oai-events')
    dc.onclose = () => dbg('data channel closed')
    dc.onmessage = (m) => { try { onEvent(JSON.parse(m.data)) } catch { /* ignore */ } }
    dc.onopen = () => {
      // a plain message (not a response-level instruction override) so the call's own instructions stay in force
      dc.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [{ type: 'input_text', text: `[${sess.greeting}]` }] } }))
      dc.send(JSON.stringify({ type: 'response.create' }))
      setState('thinking')
    }
    rt.current = { ...rt.current, pc, dc, ac, mic, audioEl, start: Date.now(), pendingEnd: false, institution: sess.kb_id }
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    const r = await fetch('https://api.openai.com/v1/realtime/calls', {
      method: 'POST', body: offer.sdp, headers: { Authorization: `Bearer ${sess.secret}`, 'Content-Type': 'application/sdp' },
    })
    if (!r.ok) throw new Error('Could not open the voice connection: ' + (await r.text()).slice(0, 200))
    await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() })
  }

  function teardown() {
    stopHold()
    const r = rt.current
    try { r.speaker?.stop(); r.dc?.close(); r.pc?.close(); r.conv?.endSession?.() } catch { /* ignore */ }
    r.mic?.getTracks().forEach((t) => t.stop())
    if (r.audioEl) { r.audioEl.srcObject = null }
    try { r.ac?.close() } catch { /* ignore */ }
  }

  async function accept() {
    if (!call) return
    setLines([]); linesRef.current = []; setErr(''); setSecs(0); setState('thinking'); setMicOn(true); setMuted(false)
    callRef.current = call
    setPhase('connecting')
    try {
      await api.post(`/phone/${call.id}/answer`)
      await connect(call)
      setPhase('incall')
    } catch (e) {
      setErr(e.message); teardown()
      await api.post(`/phone/${call.id}/hangup`).catch(() => {})
      callRef.current = null
      setPhase('idle'); setCall(null)
    }
  }
  async function decline() {
    if (!call) return
    await api.post(`/phone/${call.id}/decline`)
    setCall(null); setPhase('idle')
  }
  async function hangup() {
    const c = callRef.current
    if (!c || c.__ending) return
    c.__ending = true
    dbg('hangup', `after ${Math.round(tNow())}s, customer lines: ${linesRef.current.filter((l) => l.role === 'customer' && l.text).length}`)
    rt.current.pendingEnd = false
    setPhase('ended')
    const r = rt.current
    if (r.eleven) {
      const convId = r.conversationId
      teardown()
      let synced = false
      try {
        if (convId) {
          const x = await Promise.race([api.post(`/phone/${c.id}/eleven-sync`, { conversation_id: convId }), new Promise((_, rej) => setTimeout(() => rej(new Error('sync timeout')), 30000))])
          synced = x.lines > 0
        }
      } catch (e) { dbg('sync failed', e.message) }
      try { if (!synced) await api.post(`/phone/${c.id}/transcript`, { lines: linesRef.current.filter((l) => l.text).map(({ role, text, t }) => ({ role, text, t })) }) } catch { /* ignore */ }
      try { await api.post(`/phone/${c.id}/hangup`) } catch { /* ignore */ }  // always end the call, even if the sync above failed
      callRef.current = null
      setTimeout(() => { setCall(null); setLines([]); setPhase('idle') }, 2600)
      return
    }
    // finish the call (the summary is written server-side from the transcript; audio is never recorded)
    teardown()
    try {
      await api.post(`/phone/${c.id}/transcript`, { lines: linesRef.current.filter((l) => l.text).map(({ role, text, t }) => ({ role, text, t })) })
      await api.post(`/phone/${c.id}/hangup`)
    } catch { /* ignore */ }
    callRef.current = null
    setTimeout(() => { setCall(null); setLines([]); setPhase('idle') }, 2600)
  }

  const toggleMic = () => {
    if (rt.current.eleven) { const on = !micOn; rt.current.conv?.setMicMuted(!on); setMicOn(on); return }
    const track = rt.current.mic?.getAudioTracks()[0]
    if (!track) return
    track.enabled = !track.enabled
    setMicOn(track.enabled)
  }
  const toggleSpeaker = () => {
    if (rt.current.eleven) { const m = !muted; rt.current.conv?.setVolume({ volume: m ? 0 : 1 }); setMuted(m); return }
    setMuted((m) => { if (rt.current.audioEl) rt.current.audioEl.muted = !m; rt.current.speaker?.setMuted(!m); return !m })
  }
  const submitText = (e) => {
    e.preventDefault()
    const t = text.trim()
    if (rt.current.eleven) { if (!t) return; rt.current.conv?.sendUserMessage(t); setText(''); setState('thinking'); return }
    const dc = rt.current.dc
    if (!t || !dc || dc.readyState !== 'open') return
    const id = 'typed-' + Date.now()
    upsert(id, 'customer', { text: t }); save()
    if (rt.current.active) { dc.send(JSON.stringify({ type: 'response.cancel' })); rt.current.speaker?.stop() }
    dc.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [{ type: 'input_text', text: t }] } }))
    whenIdle(() => dc.send(JSON.stringify({ type: 'response.create' })))
    setText(''); setState('thinking')
  }

  const [site, setSite] = useState({ url: '', name: 'Alex', text: '', paste: false, busy: false, msg: '', err: false })
  const callFromSite = async () => {
    if (!site.url.trim() || site.busy) return
    setSite((s) => ({ ...s, busy: true, msg: '', err: false }))
    try {
      const r = await api.post('/demo/website', { url: site.url.trim(), name: site.name, text: site.text })
      setSite((s) => ({ ...s, busy: false, msg: `Read ${r.pages.length} page${r.pages.length === 1 ? '' : 's'} of ${r.company}. Calling you now…` }))
    } catch (e) { setSite((s) => ({ ...s, busy: false, msg: e.message, err: true, paste: true })) }
  }

  const placeCall = async () => {
    if (!pick.id) return
    try { await api.post('/calls/test', { contact_id: +pick.id, objective: pick.objective }); setToast('Calling now…') } catch (e) { setToast(e.message) }
  }

  const role = call?.role

  return (
    <div className="mock">
      <div className="mock-top">
        <Link to="/" className="link">← Back</Link>
        <b>{website ? "Website test" : "Softphone"}</b>
      </div>
      <div className="mock-grid">
        <section className="mock-side">
          {phase === 'incall' && voiceNote && <p className="small" style={{ padding: '6px 10px', background: 'var(--soft)', borderRadius: 10 }}>Voice: {voiceNote}</p>}
          <h3>You are playing</h3>
          {role ? (
            <>
              
              <div className="brief">
                <b dir="auto">{role.name}</b>
                <span>{role.language} · {role.phone}</span>
                {role.company && <span dir="auto">{role.company}</span>}
                {Object.entries(role.extra || {}).map(([k, v]) => <span key={k}>{k}: {v}</span>)}
                {role.notes && <span dir="auto">“{role.notes}”</span>}
              </div>
              {role.objective && <><p className="muted small">The agent wants to:</p><p className="small">{role.objective}</p></>}
            </>
          ) : <p className="muted">When a call rings you’ll see who the customer is here, so you can role-play them.</p>}
        </section>

        <div className="phone-wrap">
          <div className="iphone">
            <i className="btn-l a" /><i className="btn-l b" /><i className="btn-l c" /><i className="btn-r" />
            <div className="screen">
              <div className="island" />
              <div className="status"><b>{now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: false })}</b>
                <span><svg width="17" height="11" viewBox="0 0 17 11" fill="currentColor"><rect x="0" y="7" width="3" height="4" rx=".6" /><rect x="4.5" y="5" width="3" height="6" rx=".6" /><rect x="9" y="2.5" width="3" height="8.5" rx=".6" /><rect x="13.5" y="0" width="3" height="11" rx=".6" /></svg>
                  <svg width="24" height="11" viewBox="0 0 24 11"><rect x=".5" y=".5" width="20" height="10" rx="3" fill="none" stroke="currentColor" opacity=".5" /><rect x="2" y="2" width="15" height="7" rx="1.8" fill="currentColor" /><rect x="21.5" y="3.5" width="2" height="4" rx="1" fill="currentColor" opacity=".5" /></svg></span></div>

              {(phase === 'idle' || phase === 'ended' || phase === 'connecting') && (
                <div className="lock">
                  {phase === 'ended' ? (
                    <div className="ended"><b>Call ended</b><span>{dur(secs)}</span><small>Writing the summary…</small></div>
                  ) : phase === 'connecting' ? (
                    <div className="ended"><b>Connecting…</b><small>Allow the microphone if asked</small></div>
                  ) : (
                    <>
                      <small>{now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}</small>
                      <h1>{now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: false })}</h1>
                      <p className="wait"><i className="dot live" /> Ready for calls</p>
                    </>
                  )}
                  <div className="lock-foot"><span /><span /></div>
                </div>
              )}

              {phase === 'ringing' && (
                <div className="ringing">
                  <div className="who"><small>Incoming call</small><h2>{call?.caller_id}</h2><small>mobile · attempt {call?.attempt}</small></div>
                  <div className="pulse-rings"><i /><i /><i /></div>
                  <div className="answer-row">
                    <button className="red" onClick={decline} aria-label="Decline"><svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor" style={{ transform: 'rotate(135deg)' }}><path d="M6.6 10.8a15 15 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11 11 0 0 0 3.6.6 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.6 3.6a1 1 0 0 1-.25 1z" /></svg><span>Decline</span></button>
                    <button className="green" onClick={accept} aria-label="Accept"><svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor"><path d="M6.6 10.8a15 15 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11 11 0 0 0 3.6.6 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.6 3.6a1 1 0 0 1-.25 1z" /></svg><span>Accept</span></button>
                  </div>
                </div>
              )}

              {phase === 'incall' && (
                <div className="incall">
                  <div className="call-h">
                    <h2>{call?.caller_id}</h2>
                    <span className="mono">{dur(secs)}</span>{onHold && <span className="tag doing">On hold</span>}
                    <small className={state === 'speaking' ? 'on' : ''}>{onHold ? `${agentName} is checking with the team…` : state === 'speaking' ? <><i className="mini-eq"><b /><b /><b /><b /></i> {agentName} is speaking</> : state === 'thinking' ? `${agentName} is thinking…` : micOn ? 'Listening… just talk' : 'Mic is off'}</small>
                  </div>
                  <div className="chat" ref={scroller}>
                    {lines.filter((l) => l.text).map((l, i) => <div key={i} className={`b ${l.role}`} dir="auto">{l.text}</div>)}
                    {err && <div className="b err">{err}</div>}
                  </div>
                  <form className="type" onSubmit={submitText}>
                    <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Or type a reply…" dir="auto" />
                    <button type="submit" disabled={!text.trim()} aria-label="Send">↑</button>
                  </form>
                  <div className="ctrl">
                    <button className={`c ${muted ? 'on' : ''}`} onClick={toggleSpeaker} aria-label="Mute speaker">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 9v6h4l5 4V5L8 9z" />{muted ? <path d="M17 9l5 6M22 9l-5 6" /> : <path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" />}</svg><span>speaker</span></button>
                    <button className={`c mic ${micOn ? '' : 'on'}`} onClick={toggleMic} aria-label="Mute microphone">
                      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />{!micOn && <path d="M4 4l16 16" />}</svg><span>{micOn ? 'mic on' : 'muted'}</span></button>
                    <button className="c end" onClick={hangup} aria-label="End call">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" style={{ transform: 'rotate(135deg)' }}><path d="M6.6 10.8a15 15 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11 11 0 0 0 3.6.6 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.6 3.6a1 1 0 0 1-.25 1z" /></svg><span>end</span></button>
                  </div>
                </div>
              )}
              <div className="home-bar" />
            </div>
          </div>
        </div>

        <section className="mock-side">
          {website ? (<>
          <h3>Call from a website</h3>
          <p className="muted small">Paste a company website. The agent reads it, then phones you as someone from that company.</p>
          <label className="f">Website<input value={site.url} onChange={(e) => setSite({ ...site, url: e.target.value })} placeholder="https://example.com" onKeyDown={(e) => e.key === 'Enter' && callFromSite()} /></label>
          <label className="f">Your first name<input value={site.name} onChange={(e) => setSite({ ...site, name: e.target.value })} /></label>
          <button className="btn solid" onClick={callFromSite} disabled={!site.url.trim() || site.busy}>{site.busy ? 'Reading the website…' : 'Read site and call me'}</button>
          {site.paste && <label className="f">Or paste the site's text here<textarea rows="4" value={site.text} onChange={(e) => setSite({ ...site, text: e.target.value })} placeholder="Copy text from the site's pages and paste it" /></label>}
          {site.msg && <p className={site.err ? 'err small' : 'ok small'}>{site.msg}</p>}
          </>) : (<>
          <h3>Place a call</h3>
          <label className="f">Customer
            <select value={pick.id} onChange={(e) => setPick({ ...pick, id: e.target.value })}>
              {contacts.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.language}</option>)}
            </select>
          </label>
          <label className="f">Objective<textarea rows="3" value={pick.objective} onChange={(e) => setPick({ ...pick, objective: e.target.value })} /></label>
          <button className="btn solid" onClick={placeCall} disabled={!pick.id}>Call this phone</button>
          {!contacts.length && <p className="muted small">No customers yet — upload your customer list first.</p>}
          {toast && <p className="ok small">{toast}</p>}
          <p className="muted small">Campaign calls ring here. Answer, then talk with the mic or type.</p>
          </>)}
        </section>
      </div>
    </div>
  )
}
