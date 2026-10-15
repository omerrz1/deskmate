import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { api } from './api'

const Site = lazy(() => import('./site/Site'))  // the public home page, only downloaded by visitors who are not signed in

const Ctx = createContext(null)
export const useAuth = () => useContext(Ctx)

function Card({ title, sub, children }) {
  return (
    <main className="auth-wrap">
      <div className="auth-card">
        <Link to="/" className="auth-brand" aria-label="Deskmates home"><span className="orb" /><div><b>Deskmates</b><small>Your AI team</small></div></Link>
        <h1>{title}</h1>
        {sub && <p className="muted">{sub}</p>}
        {children}
      </div>
    </main>
  )
}

function Login({ onDone, notice, onSignup }) {
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [code, setCode] = useState('')
  const [second, setSecond] = useState(false)  // the password was right; now the code from the authenticator app
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr('')
    try {
      const r = await api.post('/auth/login', { username: u, password: p, code })
      if (r.need_code) setSecond(true); else onDone()
    } catch (x) { setErr(x.message); if (!second) setP(''); setCode('') }
    setBusy(false)
  }
  return (
    <Card title={second ? 'Enter your code' : 'Sign in'} sub={second ? 'Open your authenticator app and type the 6-digit code, or use one of your backup codes.' : 'Sign in to your company workspace.'}>
      {notice && <div className="notice">{notice}</div>}
      <form className="form" onSubmit={submit}>
        {!second ? (
          <>
            <label>Email or username<input autoFocus type="text" inputMode="email" autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} /></label>
            <label>Password<input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} /></label>
          </>
        ) : (
          <label>Code<input autoFocus inputMode="numeric" autoComplete="one-time-code" placeholder="123456" value={code} onChange={(e) => setCode(e.target.value)} /></label>
        )}
        {err && <p className="warn" role="alert">{err}</p>}
        <button className="btn solid" disabled={busy || !u || !p || (second && !code)}>{busy ? 'Signing in…' : second ? 'Verify' : 'Sign in'}</button>
        {second && <button type="button" className="link" onClick={() => { setSecond(false); setCode(''); setP(''); setErr('') }}>Use a different account</button>}
      </form>
      {onSignup && !second && <p className="small muted" style={{ marginTop: 14 }}>New here? <button type="button" className="link" onClick={onSignup}>Create your company workspace</button></p>}
    </Card>
  )
}

const TEAMS = [['', 'No team yet, I will add people myself'], ['starter', 'A starter trio: manager, caller and assistant'], ['sales', 'Sales team'], ['support', 'Customer support'], ['backoffice', 'Back office'], ['marketing', 'Marketing team'], ['recruiting', 'Recruiting team']]

function Signup({ onDone, onBack }) {
  const [f, setF] = useState({ company: '', email: '', password: '', again: '', team: 'starter' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const submit = async (e) => {
    e.preventDefault(); setErr('')
    if (f.password !== f.again) return setErr('The two passwords do not match.')
    setBusy(true)
    try { await api.post('/auth/signup', { company: f.company, email: f.email, password: f.password, team: f.team }); onDone() } catch (x) { setErr(x.message) }
    setBusy(false)
  }
  return (
    <Card title="Create your workspace" sub="Your own company space: your team of AI employees, files, apps and keys, private to you.">
      <form className="form" onSubmit={submit}>
        <label>Company name<input autoFocus autoComplete="organization" value={f.company} onChange={set('company')} placeholder="e.g. Northwind Plumbing" /></label>
        <label>Your email <span className="faint">(you sign in with it)</span><input type="email" autoComplete="username" value={f.email} onChange={set('email')} /></label>
        <label>Password <span className="faint">(12+ characters)</span><input type="password" autoComplete="new-password" value={f.password} onChange={set('password')} /></label>
        <label>Repeat password<input type="password" autoComplete="new-password" value={f.again} onChange={set('again')} /></label>
        <label>Start with<select value={f.team} onChange={set('team')}>{TEAMS.map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></label>
        {err && <p className="warn" role="alert">{err}</p>}
        <button className="btn solid" disabled={busy || !f.company.trim() || !f.email || !f.password}>{busy ? 'Creating…' : 'Create workspace'}</button>
        <p className="small muted">Next you connect your own OpenAI account (and ElevenLabs for phone calls), then your team is set up for you.</p>
        <button type="button" className="link" onClick={onBack}>I already have an account</button>
      </form>
    </Card>
  )
}

function MustChange({ username, onDone, onLeave }) {
  const [f, setF] = useState({ current: '', next: '', again: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault(); setErr('')
    if (f.next !== f.again) return setErr('The two passwords do not match.')
    setBusy(true)
    try { await api.post('/auth/password', { current: f.current, new: f.next }); onDone() } catch (x) { setErr(x.message) }
    setBusy(false)
  }
  return (
    <Card title="Choose your own password" sub={`Welcome, ${username}. The password you were given is temporary, so pick one that only you know.`}>
      <form className="form" onSubmit={submit}>
        <label>Temporary password<input autoFocus type="password" autoComplete="current-password" value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} /></label>
        <label>New password <span className="faint">(12+ characters)</span><input type="password" autoComplete="new-password" value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} /></label>
        <label>Repeat new password<input type="password" autoComplete="new-password" value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} /></label>
        {err && <p className="warn">{err}</p>}
        <div className="actions"><button className="btn solid" disabled={busy || !f.current || !f.next}>{busy ? 'Saving…' : 'Save and continue'}</button><button type="button" className="btn" onClick={onLeave}>Sign out</button></div>
      </form>
    </Card>
  )
}

export function AuthGate({ children }) {
  const [st, setSt] = useState(null)
  const [notice, setNotice] = useState('')
  const leaving = useRef(false)
  const loc = useLocation()
  const nav = useNavigate()
  const load = useCallback(async () => {
    try { setSt(await api.get('/auth/status')) } catch { setSt({ error: true }) }
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const h = () => {
      if (leaving.current) return  // we signed out on purpose; the 401s from closing pages are expected
      setNotice('Your session ended. Please sign in again.'); setSt((s) => (s ? { ...s, authenticated: false } : s))
    }
    window.addEventListener('auth-expired', h)
    return () => window.removeEventListener('auth-expired', h)
  }, [])

  if (!st) return <div className="auth-wrap" aria-busy="true" />
  if (st.error) return <Card title="Can't reach the server"><p className="muted">Check that the backend is running, then reload.</p></Card>
  const path = loc.pathname.replace(/\/+$/, '') || '/'
  if (st.authenticated && (path === '/signin' || path === '/signup')) return <Navigate to="/" replace />
  if (!st.authenticated) {
    // visitors see the public home page at "/", the sign-in or sign-up form at /signin and /signup, and the sign-in form anywhere else
    if (path === '/' && !notice) return <Suspense fallback={<div className="auth-wrap" aria-busy="true" />}><Site page="home" status={st} /></Suspense>
    if (path === '/signup' && st.signup_open) return <Signup onDone={() => { setNotice(''); leaving.current = false; load() }} onBack={() => nav('/signin')} />
    return <Login notice={notice} onSignup={st.signup_open ? () => nav('/signup') : null} onDone={() => { setNotice(''); leaving.current = false; load() }} />
  }
  const logout = async () => {
    leaving.current = true
    try { await api.post('/auth/logout') } catch { /* ignore */ }
    setNotice(''); nav('/', { replace: true }); await load()
    setTimeout(() => { leaving.current = false }, 1500)
  }
  if (st.must_change) return <MustChange username={st.username} onDone={load} onLeave={logout} />
  return <Ctx.Provider value={{ username: st.username, role: st.role, isOwner: st.role === 'owner', workspace: st.workspace, platform: !!st.platform, logout }}>{children}</Ctx.Provider>
}
