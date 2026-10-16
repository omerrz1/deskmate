import { useEffect, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { api } from '../api'
import Icon from '../Icon'
import { useTheme } from '../theme'
import { About, Contact, Home, Legal, Product, Security } from './pages'
import './site.css'

/** The public side of Deskmates: what it is, how it keeps your data safe, who is behind it, and how to get in touch.
 *  Visitors who are not signed in land on the home page; anyone signed in goes straight to their workspace. */

const NAV = [['/product', 'Product'], ['/security', 'Security'], ['/about', 'About'], ['/contact', 'Contact']]
const META = {
  home: ['Deskmates: a team of AI employees for your business', 'Give your business a team of AI employees. Each one has a name, an email address, a phone and a virtual computer of their own. They handle email, projects and documents while you stay in charge.'],
  product: ['Product', 'What your AI team can do: every employee gets a virtual computer, plus email, voice conversations, meetings, projects, over 200 connected apps and a browser.'],
  security: ['Security and privacy', 'How Deskmates keeps each company separate, protects sign-ins and keys, keeps you in control of what your team does, and never records calls.'],
  about: ['About', 'Our mission: give every small business a capable, trustworthy team without the cost and complexity of a big one.'],
  contact: ['Contact', 'Questions, help with your account or a security report: write to the Deskmates team.'],
  privacy: ['Privacy policy', 'What Deskmates collects, why, who processes it, and how you can delete it.'],
  terms: ['Terms of use', 'The terms for using Deskmates.'],
}

function setMeta(page) {
  const [t, d] = META[page] || META.home
  document.title = page === 'home' ? t : `${t} · Deskmates`
  let m = document.querySelector('meta[name="description"]')
  if (!m) { m = document.createElement('meta'); m.name = 'description'; document.head.appendChild(m) }
  m.content = d
}

function Header({ st }) {
  const [theme, setTheme] = useTheme()
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  useEffect(() => setOpen(false), [loc.pathname])
  useEffect(() => {
    if (!open) return
    const k = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open])
  const next = theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system'
  const signedIn = !!st?.authenticated
  const canSignup = st ? (st.signup_public ?? st.signup_open) !== false : true  // public sign-up buttons are hidden while a team access code is needed
  return (
    <header className="s-head">
      <div className="s-wrap s-head-in">
        <Link to="/" className="s-brand" aria-label="Deskmates home"><span className="s-mark"><i /></span>Deskmates</Link>
        <nav className={`s-nav ${open ? 'open' : ''}`} id="s-nav" aria-label="Main">
          {NAV.map(([to, label]) => <NavLink key={to} to={to}>{label}</NavLink>)}
          {st && !signedIn && <div className="s-nav-cta"><Link className="s-btn" to="/signin">Sign in</Link>{canSignup && <Link className="s-btn solid" to="/signup">Create workspace</Link>}</div>}
          {signedIn && <div className="s-nav-cta"><Link className="s-btn solid" to="/">Open your workspace</Link></div>}
        </nav>
        <div className="s-head-act">
          <button className="s-icon" onClick={() => setTheme(next)} aria-label={`Theme: ${theme}. Click to change.`} title={`Theme: ${theme === 'system' ? 'matches your device' : theme}`}><Icon name={theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'monitor'} size={17} /></button>
          {st && !signedIn && <Link className="s-link s-hide-sm" to="/signin">Sign in</Link>}
          {st && !signedIn && canSignup && <Link className="s-btn solid s-hide-sm" to="/signup">Create workspace</Link>}
          {signedIn && <Link className="s-btn solid s-hide-sm" to="/">Open your workspace <Icon name="arrow" size={15} /></Link>}
          <button className="s-icon s-menu" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="s-nav" aria-label={open ? 'Close menu' : 'Open menu'}><Icon name={open ? 'x' : 'menu'} size={20} /></button>
        </div>
      </div>
    </header>
  )
}

function Footer({ st }) {
  const signedIn = !!st?.authenticated
  return (
    <footer className="s-foot">
      <div className="s-wrap">
        <div className="s-foot-grid">
          <div className="s-foot-about">
            <Link to="/" className="s-brand"><span className="s-mark"><i /></span>Deskmates</Link>
            <p>A team of AI employees for your business. You stay in charge.</p>
          </div>
          <nav aria-label="Product"><h2>Product</h2><Link to="/product">What it does</Link><Link to="/security">Security</Link></nav>
          <nav aria-label="Company"><h2>Company</h2><Link to="/about">About</Link><Link to="/contact">Contact</Link></nav>
          <nav aria-label="Legal"><h2>Legal</h2><Link to="/privacy">Privacy</Link><Link to="/terms">Terms</Link></nav>
          <nav aria-label="Account"><h2>Account</h2>{signedIn ? <Link to="/">Open your workspace</Link> : <><Link to="/signin">Sign in</Link>{(st?.signup_public ?? st?.signup_open) !== false && <Link to="/signup">Create workspace</Link>}</>}</nav>
        </div>
        <p className="s-copy">© {new Date().getFullYear()} Deskmates. Calls are never recorded.</p>
      </div>
    </footer>
  )
}

export default function Site({ page = 'home', status = null }) {
  const [st, setSt] = useState(status)
  const loc = useLocation()
  useEffect(() => { if (!status) api.get('/auth/status').then(setSt).catch(() => setSt({ authenticated: false, signup_open: true })) }, [status])
  useEffect(() => { if (status) setSt(status) }, [status])
  useEffect(() => { setMeta(page); window.scrollTo(0, 0) }, [page, loc.pathname])
  const body = {
    home: <Home st={st} />, product: <Product st={st} />, security: <Security st={st} />, about: <About st={st} />,
    contact: <Contact st={st} />, privacy: <Legal which="privacy" />, terms: <Legal which="terms" />,
  }[page] || <Home st={st} />
  return (
    <div className="site">
      <a className="s-skip" href="#s-main">Skip to content</a>
      <Header st={st} />
      <main id="s-main">{body}</main>
      <Footer st={st} />
    </div>
  )
}
