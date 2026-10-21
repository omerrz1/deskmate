import WorkspaceSwitcher from './WorkspaceSwitcher'
import { useEffect, useState } from 'react'
import { api } from './api'

const STEPS = [
  ['openai', 'Connect OpenAI', 'Your team thinks, writes, plans and talks in meetings with your own OpenAI account. You pay OpenAI directly, and cheap models are used for background work.', 'platform.openai.com/api-keys', 'https://platform.openai.com/api-keys', 'sk-…'],
  ['eleven', 'Choose your call voice', 'OpenAI already powers your team and WhatsApp calls. ElevenLabs is optional if you want its voice engine for calls; it is not needed to connect Twilio or make calls.', 'elevenlabs.io → Profile → API keys', 'https://elevenlabs.io/app/settings/api-keys', 'sk_…'],
]

/** OpenAI is required; ElevenLabs is an optional call-voice provider. */
export default function SetupGate({ children }) {
  const [s, setS] = useState(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const load = () => api.get('/setup').then(setS).catch(() => setS({ complete: true }))  // if it cannot be read, do not lock anyone out
  useEffect(() => { load(); window.addEventListener('setup-needed', load); return () => window.removeEventListener('setup-needed', load) }, [])
  if (!s) return null
  if (s.complete) return children
  const at = s.step === 'eleven' ? 1 : 0
  const [, title, why, where, url, hint] = STEPS[at]
  const connect = async (e) => {
    e.preventDefault(); setBusy(true); setErr('')
    try { setS(await api.post(`/setup/${STEPS[at][0]}`, { key })); setKey('') } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  const skip = async () => { try { setS(await api.post('/setup/eleven/skip')) } catch (x) { setErr(x.message) } }
  return (
    <div className="setup">
      <div className="setup-card">
        <WorkspaceSwitcher />
        <ol className="setup-steps" aria-label="Setup steps">
          {STEPS.map(([k, t], i) => <li key={k} className={i < at ? 'past' : i === at ? 'on' : ''}><i>{i < at ? '✓' : i + 1}</i>{t}</li>)}
          <li><i>3</i>Your company</li>
        </ol>
        <h1>{title}</h1>
        <p className="small muted">Connect once for your company. All workspaces use this account.</p>
        <p className="muted">{why}</p>
        {s.can_manage === false ? <p className="notice">Your company admin needs to connect the company’s AI account. Once connected, it will work in all your workspaces automatically.</p> : <form onSubmit={connect}>
          <label className="f">API key
            <input type="password" autoComplete="off" spellCheck="false" autoFocus value={key} onChange={(e) => setKey(e.target.value)} placeholder={hint} />
          </label>
          <p className="small muted">Get one at <a href={url} target="_blank" rel="noopener noreferrer">{where}</a>. It is checked with the provider, then stored encrypted on this server and never shown again.</p>
          {err && <p className="warn" role="alert">{err}</p>}
          <div className="actions"><button className="btn solid big" disabled={busy || key.trim().length < 20}>{busy ? 'Checking…' : 'Connect'}</button>
          {at === 1 && <button type="button" className="btn ghost" onClick={skip}>Continue with OpenAI voice</button>}</div>
        </form>}
      </div>
    </div>
  )
}
