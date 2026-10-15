import { api } from './api'

const ALL = /\b(everyone|everybody|all of you|you guys|guys|folks|each of you|you all|both of you|one by one|roll call|introduce yourselves)\b/i
const BENIGN = /active response|Cancellation failed|no active response|buffer is empty/i

/**
 * Live voice for a meeting: one OpenAI Realtime session per agent (each with its own voice, personality and tools).
 * One person: the agent hears you directly and answers. Several agents: the first agent listens, and the server decides who answers
 * (a name, "everyone", the person whose task it is, or the manager), ONE voice at a time: nobody starts until the speaker's whole
 * turn (words, tool calls and the words after them) is over, and what each said is shared with the others.
 */
export function createRT({ meetingId, agents, group, h }) {
  const S = {} // agent_id -> session
  let mic = null
  let closed = false
  let queue = Promise.resolve()
  let epoch = 0
  const order = agents.map((a) => a.id)
  const nameOf = (id) => agents.find((a) => a.id === id)?.name || 'Colleague'

  const lastSpoken = [] // recent things agents said, so the mic picking up the speakers isn't mistaken for the admin
  const words = (t) => (t.toLowerCase().match(/[a-z0-9']{2,}/g) || [])
  const isEcho = (t) => {
    const w = words(t)
    if (w.length < 3) return false
    const now = Date.now()
    const recent = lastSpoken.filter((x) => now - x.at < 8000 || x.live)
    return recent.some((x) => { const aw = new Set(words(x.text)); return w.filter((k) => aw.has(k)).length / w.length >= 0.6 })
  }
  const active = (s) => s.talking || s.responding || s.calling || s.expect
  const anyActive = (except) => Object.values(S).some((o) => o !== except && active(o))
  async function floor(except) { // wait until nobody else has the floor
    const until = Date.now() + 20000
    while (anyActive(except) && Date.now() < until) await new Promise((r) => setTimeout(r, 120))
  }
  function endTurn(s) { const t = s.turn; if (!t) return; clearTimeout(t.timer); s.turn = null; t.resolve() }
  const settled = (s) => !s.talking && !s.responding && !s.calling && !s.expect
  function maybeEnd(s) { if (s.turn && settled(s)) endTurn(s) }

  const send = (s, o) => { if (s.dc?.readyState === 'open') s.dc.send(JSON.stringify(o)) }
  const item = (s, text) => send(s, { type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [{ type: 'input_text', text }] } })

  async function connect(a) {
    const sess = await api.post(`/meetings/${meetingId}/realtime`, { agent_id: a.id })
    if (closed) return
    const pc = new RTCPeerConnection()
    const au = new Audio(); au.autoplay = true
    pc.ontrack = (e) => { au.srcObject = e.streams[0] }
    pc.addTrack(mic.getAudioTracks()[0], mic)
    const dc = pc.createDataChannel('oai-events')
    const s = { id: a.id, pc, dc, au, busy: false, talking: false, responding: false, calling: false, expect: false, turn: null, tools: [], buf: '', listener: sess.listener }
    S[a.id] = s
    const opened = new Promise((res, rej) => { dc.onopen = res; setTimeout(() => rej(new Error('voice connection timed out')), 15000) })
    dc.onmessage = (m) => { try { onEvent(s, JSON.parse(m.data)) } catch { /* ignore */ } }
    pc.onconnectionstatechange = () => { if (!closed && pc.connectionState === 'failed') h.onErr(`${a.name}'s connection dropped`) }
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    const r = await fetch('https://api.openai.com/v1/realtime/calls', { method: 'POST', body: offer.sdp, headers: { Authorization: `Bearer ${sess.secret}`, 'Content-Type': 'application/sdp' } })
    if (!r.ok) throw new Error('Could not open the voice connection')
    await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() })
    await opened
  }

  async function start() {
    mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    await Promise.all(agents.map(connect))
    if (closed) close()
  }

  function onEvent(s, ev) {
    switch (ev.type) {
      case 'input_audio_buffer.speech_started':
        if (s.listener) { h.onRec(true); interruptAll() }
        break
      case 'input_audio_buffer.speech_stopped': if (s.listener) h.onRec(false); break
      case 'conversation.item.input_audio_transcription.completed': {
        const t = (ev.transcript || '').trim()
        if (s.listener && t && !isEcho(t)) { h.onAdmin(t); if (group) route(t, true) }
        break
      }
      case 'response.created': s.busy = true; s.responding = true; s.expect = false; h.onThinking(s.id); break
      case 'response.output_audio_transcript.delta':
        s.buf += ev.delta || ''; h.onPartial(s.id, s.buf); h.onCaption(s.id, s.buf); break
      case 'response.output_audio_transcript.done': {
        const t = (ev.transcript || s.buf || '').trim()
        s.buf = ''
        h.onPartial(null, '')
        if (t) {
          lastSpoken.push({ text: t, at: Date.now(), live: true, id: s.id }); if (lastSpoken.length > 6) lastSpoken.shift()
          h.onAgent(s.id, t, s.tools.splice(0))
          for (const o of Object.values(S)) if (o.id !== s.id) item(o, `[${nameOf(s.id)} said]: ${t}`)
        }
        break
      }
      case 'output_audio_buffer.started': s.talking = true; h.onSpeaking(s.id); break
      case 'output_audio_buffer.stopped': s.talking = false; for (const x of lastSpoken) if (x.id === s.id) { x.live = false; x.at = Date.now() } h.onSpeaking(null); maybeEnd(s); break
      case 'response.output_item.done':
        if (ev.item?.type === 'function_call') (s.pending = s.pending || []).push(ev.item)
        break
      case 'response.done': {
        s.busy = false; s.responding = false; h.onThinking(null)
        const calls = s.pending || []; s.pending = []
        if (calls.length) { s.calling = true; runTools(s, calls) } // the turn continues after the tools: don't give the floor away yet
        else setTimeout(() => maybeEnd(s), 1200) // an answer with no audio has nothing to wait for
        break
      }
      case 'error':
        if (!BENIGN.test(ev.error?.message || '')) h.onErr(ev.error?.message || 'Voice error')
        break
      default: break
    }
  }

  async function runTools(s, calls) {
    const my = epoch
    for (const c of calls) {
      let args = {}
      try { args = JSON.parse(c.arguments || '{}') } catch { /* ignore */ }
      h.onDoing(s.id, c.name)
      let out = '{"error":"tool failed"}', link = null, ok = false
      try { const r = await api.post(`/meetings/${meetingId}/tool`, { agent_id: s.id, name: c.name, args }); out = r.result; link = r.link; ok = r.ok } catch { /* keep default */ }
      s.tools.push({ name: c.name, link, ok })
      send(s, { type: 'conversation.item.create', item: { type: 'function_call_output', call_id: c.call_id, output: out } })
    }
    h.onDoing(null)
    s.calling = false
    if (my !== epoch || closed) { s.expect = false; maybeEnd(s); return } // the admin spoke meanwhile: don't talk over them
    s.expect = true
    send(s, { type: 'response.create' })
  }

  // who is being spoken to: the server decides (names, "everyone", who just answered, or by role); this is the offline fallback
  async function who(text) {
    if (agents.length === 1) return agents
    try {
      const r = await Promise.race([api.post(`/meetings/${meetingId}/route`, { text }), new Promise((_, rej) => setTimeout(() => rej(new Error('slow')), 2500))])
      const picked = agents.filter((a) => r.agent_ids.includes(a.id))
      if (picked.length) return picked
    } catch { /* fall through */ }
    const named = agents.filter((a) => new RegExp(`\\b${a.name.split(' ')[0]}\\b`, 'i').test(text))
    if (named.length) return named
    if (ALL.test(text)) return agents
    return [agents.find((a) => a.is_manager) || agents[0]]
  }

  // ask the chosen agents to answer, one after another
  function route(text, fromVoice, instruction) {
    const my = epoch
    for (const o of Object.values(S)) if (!(fromVoice && o.listener)) item(o, instruction ? text : `[Admin says]: ${text}`)
    queue = queue.then(async () => {
      const list = instruction ? agents.slice(0, 1) : await who(text)
      for (const a of list) {
        if (my !== epoch || closed) return
        const s = S[a.id]
        if (!s) continue
        await floor(s)
        if (my !== epoch || closed) return
        await new Promise((resolve) => {
          s.expect = true
          s.turn = { resolve, timer: setTimeout(() => endTurn(s), 60000) } // safety net only
          send(s, { type: 'response.create' })
        })
      }
    })
  }

  function interruptAll() {
    epoch++
    queue = Promise.resolve()
    for (const s of Object.values(S)) {
      if (s.busy) send(s, { type: 'response.cancel' })
      send(s, { type: 'output_audio_buffer.clear' })
      s.expect = false; s.calling = false; s.responding = false
      endTurn(s)
    }
    h.onSpeaking(null)
  }

  return {
    start,
    typed(text) { interruptAll(); epoch++; route(text, false) },
    greet() { route('[The admin has just joined the meeting. Greet them warmly in one short sentence and mention one useful thing you know.]', false, true) },
    setMuted(m) { mic?.getAudioTracks().forEach((t) => { t.enabled = !m }) },
    interruptAll,
    close,
  }

  function close() {
    closed = true
    for (const s of Object.values(S)) { try { s.dc?.close(); s.pc?.close(); s.au.srcObject = null } catch { /* ignore */ } }
    mic?.getTracks().forEach((t) => t.stop())
  }
}
