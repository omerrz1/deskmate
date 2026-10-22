import VoiceTest from '../VoiceTest'
import VoiceBrowser, { CurrentVoice } from '../VoiceBrowser'
import { useState } from 'react'
import { api, usePoll } from '../api'
import { Panel, Badge } from '../ui'

const LANGS = ['English', 'Arabic', 'Hindi', 'Urdu', 'Bengali', 'Chinese', 'Indonesian', 'Tamil', 'Turkish', 'Persian', 'Spanish', 'French']
const MODES = [
  ['smart', 'Smart (recommended)', 'Use the phone code when it is reliable (e.g. +91 Hindi). For countries where it says little, like UK or US numbers, use the spreadsheet language if there is one.'],
  ['phone', 'Always the phone code', 'Every customer is called in their country’s main language.'],
  ['sheet', 'Always the spreadsheet', 'Ignore phone codes; use the language column.'],
]

// The panel most people want: which voice and which name the caller uses, with a list to choose from.
function CallerVoice({ profiles, reload, setMsg }) {
  const list = profiles || []
  const [pickId, setPickId] = useState(null)
  const [open, setOpen] = useState(false)
  const prof = list.find((p) => p.id === pickId) || list.find((p) => p.language === 'English' && !p.countries.length) || list[0]
  if (!prof) return null
  const save = async (patch) => {
    try {
      await api.put(`/voice-profiles/${prof.id}`, { name: prof.name, language: prof.language, countries: prof.countries, voice_id: prof.voice_id, agent_name: prof.agent_name, dialect: prof.dialect, tuning: prof.tuning || {}, ...patch })
      await reload()
      return true
    } catch (e) { setMsg(e.message); return false }
  }
  return (
    <Panel title="Caller voice and name" right={list.length > 1 ? <select value={prof.id} onChange={(e) => { setPickId(+e.target.value); setOpen(false) }} aria-label="Which profile">{list.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select> : null}>
      <p className="muted small">The voice and the first name your callers use for <b>{prof.name}</b> calls. Employees can have their own voice and name on their Settings tab; those win over this.</p>
      <div className="form-row">
        <label>Name the caller uses<input key={`${prof.id}-${prof.agent_name}`} defaultValue={prof.agent_name} placeholder="e.g. Sophie" maxLength={40}
          onBlur={async (e) => { const v = e.target.value.trim(); if (v !== (prof.agent_name || '') && await save({ agent_name: v })) setMsg('Caller name saved. It applies from the next call.') }} /></label>
        <div className="f" style={{ flex: 2 }}>Voice<div style={{ padding: '8px 0' }}><CurrentVoice voiceId={prof.voice_id} /></div></div>
      </div>
      <div className="actions"><button className="btn" onClick={() => setOpen(!open)}>{open ? 'Close the list' : 'Choose a different voice'}</button></div>
      {open && <VoiceBrowser language={prof.language} currentId={prof.voice_id} onPick={async (v) => {
        if (await save({ voice_id: v.voice_id, agent_name: prof.agent_name || v.name.split(/[ \-–—]/)[0] })) { setOpen(false); setMsg(`Calls now use ${v.name}. It applies from the next call.`) }
      }} />}
    </Panel>
  )
}

function ProfileForm({ initial, countries, onDone }) {
  const [p, setP] = useState(initial)
  const [find, setFind] = useState(false)
  const set = (k, v) => setP((x) => ({ ...x, [k]: v }))
  const toggle = (c) => set('countries', p.countries.includes(c) ? p.countries.filter((x) => x !== c) : [...p.countries, c])
  const save = async () => { p.id ? await api.put(`/voice-profiles/${p.id}`, p) : await api.post('/voice-profiles', p); onDone() }
  const relevant = countries.filter((c) => c.language === p.language || p.countries.includes(c.code))
  return (
    <Panel title={p.id ? 'Edit voice profile' : 'New voice profile'} right={<button className="link" onClick={onDone}>Close</button>}>
      <div className="form">
        <div className="form-row">
          <label>Name<input value={p.name} onChange={(e) => set('name', e.target.value)} placeholder="Arabic (Gulf)" /></label>
          <label>Language<select value={p.language} onChange={(e) => set('language', e.target.value)}>{LANGS.map((l) => <option key={l}>{l}</option>)}</select></label>
          <label>Agent’s spoken name<input value={p.agent_name} onChange={(e) => set('agent_name', e.target.value)} placeholder="Layla" /></label>
        </div>
        <label>Dialect / accent note for the agent<input value={p.dialect} onChange={(e) => set('dialect', e.target.value)} placeholder="Gulf (Khaleeji) Arabic, simple and lightly colloquial" /></label>
        <div>
          <p className="small muted">Countries that use this profile <span className="faint">(leave empty to make it the default for the language)</span></p>
          <div className="chips" style={{ marginTop: 8 }}>
            {relevant.map((c) => <button key={c.code} className={`chip ${p.countries.includes(c.code) ? 'on' : ''}`} onClick={() => toggle(c.code)}>{c.flag} {c.country} +{c.code}</button>)}
            {!relevant.length && <span className="muted small">No countries listed for this language.</span>}
          </div>
        </div>
        <div className="tune">
          <p className="small muted">Voice tuning <span className="faint">(leave on defaults unless something sounds off; applies from the next call)</span></p>
          <div className="form-row">
            <label>Pace: {p.tuning?.speed ?? 'default'}<input type="range" min="0.8" max="1.2" step="0.05" value={p.tuning?.speed ?? 1} onChange={(e) => set('tuning', { ...p.tuning, speed: +e.target.value })} /></label>
            <label>Expressiveness: {p.tuning?.stability != null ? (p.tuning.stability < 0.4 ? 'very lively' : p.tuning.stability < 0.6 ? 'natural' : 'steady') : 'default'}<input type="range" min="0.2" max="0.9" step="0.05" value={p.tuning?.stability ?? 0.5} onChange={(e) => set('tuning', { ...p.tuning, stability: +e.target.value })} /></label>
            <label>Waits before replying<select value={p.tuning?.turn_eagerness || 'normal'} onChange={(e) => set('tuning', { ...p.tuning, turn_eagerness: e.target.value })}><option value="patient">patient (lets people finish)</option><option value="normal">normal</option><option value="eager">eager (quicker replies)</option></select></label>
          </div>
          {p.tuning && Object.keys(p.tuning).length > 0 && <button className="link" onClick={() => set('tuning', {})}>Reset to defaults</button>}
        </div>
        <label>ElevenLabs voice ID
          <div className="actions"><input value={p.voice_id} onChange={(e) => set('voice_id', e.target.value)} style={{ flex: 1 }} /><button className="btn small" onClick={() => setFind(!find)}>Browse voices</button></div>
        </label>
        {find && <VoiceBrowser language={p.language} currentId={p.voice_id} onPick={(v) => { set('voice_id', v.voice_id); if (!p.agent_name) set('agent_name', v.name.split(/[ \-–—]/)[0]); setFind(false) }} />}
        <div className="actions"><button className="btn solid" disabled={!p.name || !p.voice_id} onClick={save}>Save</button></div>
      </div>
    </Panel>
  )
}

export default function Settings({ embedded = false }) {
  const [settings, reloadS] = usePoll('/settings', 0)
  const [profiles, reload] = usePoll('/voice-profiles', 0)
  const [countries] = usePoll('/countries', 0)
  const [keys] = usePoll('/setup', 0)
  const engine = settings?.voice_engine || 'elevenlabs'
  const elevenReady = keys ? !!keys.eleven?.connected : undefined  // undefined until the key status has loaded
  const [edit, setEdit] = useState(null)
  const [callVoice, setCallVoice] = useState(null)
  const [msg, setMsg] = useState('')
  const flags = Object.fromEntries((countries || []).map((c) => [c.code, c.flag]))
  const names = Object.fromEntries((countries || []).map((c) => [c.code, c.country]))
  const selectedVoice = callVoice || settings?.call_voice || 'marin'

  return (
    <div className={embedded ? "" : "page"}>
      {!embedded && <header className="page-h"><h1>Settings</h1></header>}
      {msg && <div className="notice" onClick={() => setMsg('')}>{msg}</div>}

      <Panel title="Which voice engine makes the calls?">
        <p className={`small ${engine === 'elevenlabs' && elevenReady !== false ? 'muted' : ''}`} role="status">
          {engine !== 'elevenlabs'
            ? <><b>Calls use the OpenAI voice.</b> It sounds more robotic than ElevenLabs. Choose ElevenLabs below for the natural voice.</>
            : elevenReady === false
              ? <><b>ElevenLabs is not connected, so calls fall back to the OpenAI voice.</b> Add your ElevenLabs key under Connections to get the natural voice.</>
              : <><b>Calls are set to use ElevenLabs voices.</b> The OpenAI voice further down is only a backup.</>}
        </p>
        <div className="modes">
          {[['elevenlabs', 'ElevenLabs (recommended)', 'The most natural, human-sounding voices, with a voice profile per country set up under Voice profiles below.'],
            ['openai', 'OpenAI Realtime (backup)', 'One model hears and speaks (gpt-realtime-2.1). Fast and works in every language, but sounds more synthetic.']].map(([k, t, d]) => (
            <label key={k} className={`mode-card ${engine === k ? 'on' : ''}`}>
              <input type="radio" name="engine" checked={engine === k} onChange={async () => { await api.put('/settings', { voice_engine: k }); reloadS() }} />
              <div><b>{t}</b><small>{d}</small></div>
            </label>
          ))}
        </div>
      </Panel>

      <CallerVoice profiles={profiles} reload={reload} setMsg={setMsg} />

      <Panel title="Standing instructions for every call">
        <p className="muted small">Rules every call reads again before it starts, whoever is calling and whatever the campaign: how to speak, what never to say, when to stop. Changes apply from the next call. Campaign and one-off instructions are added to these.</p>
        <textarea rows="5" style={{ width: '100%' }} key={settings?.call_instructions || 'none'} defaultValue={settings?.call_instructions || ''} maxLength={4000}
          placeholder="e.g. Keep it under two minutes. Never discuss prices. If they ask to stop, stop. Always confirm their name first."
          onBlur={async (e) => { if (e.target.value.trim() !== (settings?.call_instructions || '')) { try { await api.put('/settings', { call_instructions: e.target.value }); reloadS(); setMsg('Standing call instructions saved.') } catch (x) { setMsg(x.message) } } }} />
      </Panel>

      <Panel title="Backup voice (OpenAI)">
        <p className="muted small">Only used when ElevenLabs is switched off or can’t connect. Each call’s report says which voice it used and why.</p>
        <label>OpenAI Realtime voice
          <select value={selectedVoice} onChange={async (e) => {
            const v = e.target.value
            setCallVoice(v)
            try { await api.put('/settings', { call_voice: v }); reloadS(); setMsg('Default call voice saved.') } catch (x) { setMsg(x.message) }
          }}>
            {['marin', 'cedar', 'coral', 'sage', 'ballad', 'verse', 'ash', 'alloy', 'echo', 'shimmer'].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <span className="faint">Used when an employee or campaign has no voice selected. Employee and campaign voice choices take priority. marin and cedar sound the most natural. ElevenLabs calls use the matching language voice profile below.</span>
        </label>
        <VoiceTest voice={selectedVoice} />
      </Panel>

      <Panel title="Workspace call capacity">
        <label>Maximum simultaneous calls<select value={settings?.max_concurrent_calls || 10} onChange={async (e) => { try { await api.put('/settings', { max_concurrent_calls: +e.target.value }); reloadS(); setMsg('Workspace call capacity saved.') } catch (x) { setMsg(x.message) } }}>{[1, 2, 3, 5, 10, 15, 20, 30, 50, 75, 100].map((n) => <option key={n} value={n}>{n} at a time</option>)}</select>
          <span className="faint">Applies across campaigns and direct calls. Twilio also enforces separate account concurrency and calls-per-second limits, which this setting cannot raise.</span>
        </label>
      </Panel>

      <Panel title="Main link">
        <p className="muted small">The link your callers send when a customer is ready to move forward: a sign-up, booking, application or payment page. It is saved with the call, and your team (or a later text/email integration) delivers it.</p>
        <div className="actions">
          <input style={{ flex: 1, minWidth: 260 }} defaultValue={settings?.main_link || ''} key={settings?.main_link || 'none'} placeholder="https://…" onBlur={async (e) => { try { await api.put('/settings', { main_link: e.target.value }); reloadS(); setMsg('Main link saved.') } catch (x) { setMsg(x.message) } }} />
        </div>
      </Panel>

      <Panel title="Language">
        <label className="check"><input type="checkbox" checked={!!settings?.english_only} onChange={async (e) => { await api.put('/settings', { english_only: e.target.checked }); reloadS() }} /> English only: every call is made in English, whatever the customer's number or sheet says</label>
      </Panel>

      <Panel title="Which language does each customer get called in?">
        <div className="modes">
          {MODES.map(([k, t, d]) => (
            <label key={k} className={`mode-card ${settings?.language_source === k ? 'on' : ''}`}>
              <input type="radio" name="mode" checked={settings?.language_source === k} onChange={async () => { await api.put('/settings', { language_source: k }); reloadS() }} />
              <div><b>{t}</b><small>{d}</small></div>
            </label>
          ))}
        </div>
        <div className="actions" style={{ marginTop: 12 }}>
          <button className="btn" onClick={async () => { const r = await api.post('/contacts/detect-languages'); setMsg(`Updated ${r.updated} customers from their phone numbers. Languages you set by hand were kept.`) }}>Re-detect existing customers</button>
        </div>
      </Panel>

      {edit && countries && <ProfileForm initial={edit} countries={countries} onDone={() => { setEdit(null); reload() }} />}

      <Panel title="Voices and agents by country / language" right={<button className="btn small solid" onClick={() => setEdit({ name: '', language: 'Arabic', countries: [], voice_id: '', agent_name: '', dialect: '', tuning: {} })}>+ Add profile</button>} pad={false}>
        <p className="muted small pad" style={{ paddingBottom: 0 }}>Each profile has its own ElevenLabs agent, voice, spoken name and dialect. A call picks the profile that matches the customer’s language and phone country, otherwise the language’s default.</p>
        <table className="tbl"><tbody>
          {(profiles || []).map((p) => (
            <tr key={p.id} onClick={() => setEdit({ ...p })}>
              <td className="strong">{p.name}<small>{p.dialect}</small></td>
              <td><Badge>{p.language}</Badge></td>
              <td className="wide">{p.countries.length ? p.countries.map((c) => `${flags[c] || ''} ${names[c] || '+' + c}`).join(' · ') : <span className="muted">default for {p.language}</span>}</td>
              <td className="num">{p.agent_name || '—'}</td>
              <td className="num"><button className="link" onClick={async (e) => { e.stopPropagation(); await api.del(`/voice-profiles/${p.id}`); reload() }}>Delete</button></td>
            </tr>
          ))}
        </tbody></table>
      </Panel>
    </div>
  )
}
