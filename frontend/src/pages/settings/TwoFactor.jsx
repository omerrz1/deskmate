import { useEffect, useState } from 'react'
import { api, usePoll } from '../../api'
import { Panel } from '../../ui'
import { useToast } from '../../Feedback'

/** Two-factor sign-in with an authenticator app (Google Authenticator, 1Password, Authy…), plus one-time backup codes. */
export default function TwoFactor() {
  const toast = useToast()
  const [st, reload] = usePoll('/auth/2fa', 0)
  const [step, setStep] = useState('')        // '' | 'password' | 'scan' | 'codes' | 'off' | 'newcodes'
  const [pw, setPw] = useState('')
  const [code, setCode] = useState('')
  const [setup, setSetup] = useState(null)
  const [svg, setSvg] = useState('')
  const [codes, setCodes] = useState([])
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (setup) import('qrcode').then((Q) => Q.toString(setup.uri, { type: 'svg', margin: 1, width: 180 })).then(setSvg).catch(() => setSvg('')) }, [setup])
  if (!st) return null
  const reset = () => { setStep(''); setPw(''); setCode(''); setSetup(null); setSvg('') }
  const go = async (fn) => { setBusy(true); try { await fn() } catch (e) { toast.err(e.message) } finally { setBusy(false) } }
  return (
    <Panel title="Two-factor sign-in">
      {step === '' && (
        <>
          <p className="muted small">{st.enabled ? `On. You are asked for a code from your authenticator app each time you sign in. ${st.backup_codes_left} backup code${st.backup_codes_left === 1 ? '' : 's'} left.` : 'Off. Add a second step so a stolen password alone is not enough to get in.'}</p>
          <div className="actions" style={{ marginTop: 10 }}>
            {!st.enabled && <button className="btn solid" onClick={() => setStep('password')}>Turn on</button>}
            {st.enabled && <><button className="btn" onClick={() => setStep('newcodes')}>New backup codes</button><button className="btn danger" onClick={() => setStep('off')}>Turn off</button></>}
          </div>
        </>
      )}
      {step === 'password' && (
        <form className="form" onSubmit={(e) => { e.preventDefault(); go(async () => { setSetup(await api.post('/auth/2fa/setup', { password: pw })); setStep('scan') }) }}>
          <label>Confirm your password<input type="password" autoFocus autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
          <div className="actions"><button className="btn solid" disabled={busy || !pw}>Continue</button><button type="button" className="btn" onClick={reset}>Cancel</button></div>
        </form>
      )}
      {step === 'scan' && setup && (
        <form className="form" onSubmit={(e) => { e.preventDefault(); go(async () => { const r = await api.post('/auth/2fa/enable', { code }); setCodes(r.backup_codes); setStep('codes'); reload() }) }}>
          <p className="small">1. Scan this with your authenticator app, or type the key in by hand.</p>
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
            {svg && <div style={{ width: 180, height: 180, background: '#fff', borderRadius: 10 }} dangerouslySetInnerHTML={{ __html: svg }} />}
            <code style={{ wordBreak: 'break-all', maxWidth: 260 }}>{setup.secret}</code>
          </div>
          <label>2. Type the 6-digit code it shows<input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" /></label>
          <div className="actions"><button className="btn solid" disabled={busy || code.length < 6}>Turn on</button><button type="button" className="btn" onClick={reset}>Cancel</button></div>
        </form>
      )}
      {step === 'codes' && (
        <div className="form">
          <p className="small"><b>Save these backup codes now.</b> Each works once if you lose your phone. They are not shown again.</p>
          <pre className="docview" style={{ maxHeight: 'none', columns: 2 }}>{codes.join('\n')}</pre>
          <div className="actions"><button className="btn" onClick={() => navigator.clipboard?.writeText(codes.join('\n'))}>Copy</button><button className="btn solid" onClick={reset}>I have saved them</button></div>
        </div>
      )}
      {(step === 'off' || step === 'newcodes') && (
        <form className="form" onSubmit={(e) => { e.preventDefault(); go(async () => {
          if (step === 'off') { await api.post('/auth/2fa/disable', { password: pw, code }); toast.ok('Two-factor sign-in is off'); reset() } else { const r = await api.post('/auth/2fa/backup-codes', { password: pw, code }); setCodes(r.backup_codes); setStep('codes') }
          reload()
        }) }}>
          <label>Password<input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
          <label>Code from your app (or a backup code)<input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></label>
          <div className="actions"><button className="btn solid" disabled={busy || !pw || !code}>{step === 'off' ? 'Turn off' : 'Make new codes'}</button><button type="button" className="btn" onClick={reset}>Cancel</button></div>
        </form>
      )}
    </Panel>
  )
}
