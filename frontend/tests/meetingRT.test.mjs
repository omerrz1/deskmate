import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
const source = (await readFile(new URL('../src/meetingRT.js', import.meta.url), 'utf8')).replace("import { api } from './api'", 'const api = {post: (...args) => globalThis.testApi.post(...args)}')
const { createRT } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const tick = () => new Promise((r) => setTimeout(r, 5))
function setup(count = 1) {
  const channels = [], tracks = [{ enabled: true, stop() { this.stopped = true } }], heard = [], errors = []
  globalThis.testApi = { post: async (url, data) => url.endsWith('/realtime') ? { listener: data.agent_id === 1, secret: 'test' } : { agent_ids: [1, 2] } }
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: async () => ({ getAudioTracks: () => tracks, getTracks: () => tracks }) } } })
  globalThis.Audio = class {}
  globalThis.RTCPeerConnection = class {
    addTrack() {} addTransceiver() {}
    createDataChannel() { const dc = { readyState: 'open', sent: [], send(t) { this.sent.push(JSON.parse(t)) }, close() {} }; channels.push(dc); this.dc = dc; return dc }
    async createOffer() { return { sdp: 'test' } } async setLocalDescription() {}
    async setRemoteDescription() { this.dc.onopen() } close() {}
  }
  globalThis.fetch = async () => ({ ok: true, text: async () => 'answer' })
  const h = Object.fromEntries(['onRec', 'onPartial', 'onCaption', 'onSpeaking', 'onThinking', 'onDoing', 'onAgent'].map((k) => [k, () => {}]))
  h.onAdmin = async (t) => heard.push(t); h.onErr = (e) => errors.push(e)
  const rt = createRT({ meetingId: 1, agents: Array.from({ length: count }, (_, i) => ({ id: i + 1, name: `Agent${i + 1}` })), h })
  const emit = (ev, index = 0) => channels[index].onmessage({ data: JSON.stringify(ev) })
  const responses = (i = 0) => channels[i].sent.filter((x) => x.type === 'response.create').length
  return { rt, tracks, channels, heard, errors, emit, responses }
}
test('quiet entry, manual speech commit, one reply, and mute discards late audio', async () => {
  const x = setup(); await x.rt.start()
  try {
    assert.equal(x.tracks[0].enabled, false); assert.equal(x.responses(), 0)
    x.rt.beginTalk(); assert.equal(x.tracks[0].enabled, true)
    await new Promise((r) => setTimeout(r, 190)); x.rt.endTalk()
    assert.equal(x.tracks[0].enabled, false)
    x.emit({ type: 'input_audio_buffer.committed', item_id: 'u1' })
    x.emit({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'u1', transcript: 'Hello there' })
    await tick(); assert.deepEqual(x.heard, ['Hello there']); assert.equal(x.responses(), 1)
    x.rt.setMuted(true)
    x.emit({ type: 'input_audio_buffer.committed', item_id: 'u2' })
    x.emit({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'u2', transcript: 'Room conversation' })
    await tick(); assert.equal(x.heard.length, 1)
  } finally { x.rt.close() }
  assert.equal(x.tracks[0].stopped, true)
})
test('interruption cancels playback and prevents the next group speaker', async () => {
  const x = setup(2); await x.rt.start()
  try {
    x.rt.typed('Everyone, hello'); await tick(); assert.equal(x.responses(), 1); assert.equal(x.responses(1), 0)
    x.emit({ type: 'response.created', response: { id: 'r1' } })
    x.emit({ type: 'output_audio_buffer.started' })
    x.rt.beginTalk(); await tick()
    assert.ok(x.channels[0].sent.some((e) => e.type === 'response.cancel'))
    assert.equal(x.responses(1), 0)
    x.emit({ type: 'response.output_item.done', item: { type: 'function_call', name: 'create_document', arguments: '{}' } })
    x.emit({ type: 'response.done', response: { id: 'r1', status: 'cancelled' } })
    assert.equal(x.responses(1), 0)
  } finally { x.rt.close() }
})
test('hands-free is opt-in and mute disables the audio track', async () => {
  const x = setup(); await x.rt.start()
  try {
    x.rt.setHandsFree(true); assert.equal(x.tracks[0].enabled, false)
    x.rt.setMuted(false); assert.equal(x.tracks[0].enabled, true)
    x.emit({ type: 'input_audio_buffer.speech_started' })
    x.emit({ type: 'input_audio_buffer.speech_stopped' })
    x.emit({ type: 'input_audio_buffer.committed', item_id: 'u1' })
    x.rt.setMuted(true)
    x.emit({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'u1', transcript: 'Ignore this' })
    await tick(); assert.equal(x.responses(), 0); assert.equal(x.tracks[0].enabled, false)
  } finally { x.rt.close() }
})
