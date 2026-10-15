import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Icon from './Icon'
import { label } from './api'

export function Panel({ title, right, children, className = '', pad = true }) {
  return (
    <section className={`panel ${className}`}>
      {(title || right) && (
        <header className="panel-h">
          <h3>{title}</h3>
          <div>{right}</div>
        </header>
      )}
      <div className={pad ? 'panel-b' : ''}>{children}</div>
    </section>
  )
}

export function CountUp({ value, suffix = '', dec = 0 }) {
  const [v, setV] = useState(0)
  const from = useRef(0)
  useEffect(() => {
    const start = performance.now(), a = from.current, b = Number(value) || 0
    let raf
    const tick = (t) => {
      const p = Math.min(1, (t - start) / 700)
      const e = 1 - Math.pow(1 - p, 3)
      setV(a + (b - a) * e)
      if (p < 1) raf = requestAnimationFrame(tick)
      else from.current = b
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value])
  return <>{v.toFixed(dec)}{suffix}</>
}

export function Stat({ k, value, suffix, sub, live }) {
  return (
    <div className="stat">
      <div className="stat-k">{live && <i className="dot live" />}{k}</div>
      <div className="stat-v"><CountUp value={value} suffix={suffix} /></div>
      {sub && <div className="stat-s">{sub}</div>}
    </div>
  )
}

export function Badge({ children, kind = '' }) {
  return <span className={`badge ${kind}`}>{typeof children === 'string' ? label(children) : children}</span>
}

export function outcomeKind(o) {
  if (['success', 'payment_promised', 'appointment_booked', 'interested', 'converted'].includes(o)) return 'good'
  if (['not_interested', 'refused', 'do_not_call', 'wrong_number', 'failed', 'declined', 'invalid'].includes(o)) return 'bad'
  return ''
}

export function Bars({ data }) {
  const max = Math.max(1, ...data.map((d) => d.answered + d.missed))
  return (
    <div className="bars">
      {data.map((d, i) => (
        <div className="bar-col" key={d.day} title={`${d.day}: ${d.answered} answered, ${d.missed} missed`}>
          <div className="bar-stack">
            <div className="bar missed" style={{ height: `${(d.missed / max) * 100}%`, animationDelay: `${i * 40}ms` }} />
            <div className="bar answered" style={{ height: `${(d.answered / max) * 100}%`, animationDelay: `${i * 40}ms` }} />
          </div>
          <span>{d.day.slice(3)}</span>
        </div>
      ))}
    </div>
  )
}

export function Donut({ data, size = 150 }) {
  const total = data.reduce((a, d) => a + d.n, 0)
  const r = 54, c = 2 * Math.PI * r
  let off = 0
  const RAMP = ['#fe5011', '#ff773b', '#ffa27a', '#221d1b', '#8fb3c6', '#c9b8ae']
  const shade = (i) => RAMP[i % RAMP.length]
  return (
    <div className="donut-wrap">
      <svg width={size} height={size} viewBox="0 0 140 140" className="donut">
        <circle cx="70" cy="70" r={r} fill="none" stroke="rgba(37,31,29,.08)" strokeWidth="14" />
        {total > 0 && data.map((d, i) => {
          const len = (d.n / total) * c
          const el = (
            <circle key={d.k} cx="70" cy="70" r={r} fill="none" stroke={shade(i, data.length)} strokeWidth="14"
              strokeDasharray={`${Math.max(len - 2, 0)} ${c - len + 2}`} strokeDashoffset={-off} transform="rotate(-90 70 70)" />
          )
          off += len
          return el
        })}
        <text x="70" y="68" textAnchor="middle" className="donut-n">{total}</text>
        <text x="70" y="86" textAnchor="middle" className="donut-l">calls</text>
      </svg>
      <ul className="legend">
        {data.slice(0, 6).map((d, i) => (
          <li key={d.k}><i style={{ background: shade(i, data.length) }} />{label(d.k)}<b>{d.n}</b></li>
        ))}
        {!data.length && <li className="muted">No analysed calls yet</li>}
      </ul>
    </div>
  )
}

export function HBars({ data }) {
  const max = Math.max(1, ...data.map((d) => d.n))
  return (
    <div className="hbars">
      {data.map((d) => (
        <div key={d.k} className="hbar">
          <span>{label(d.k)}</span>
          <div><i style={{ width: `${(d.n / max) * 100}%` }} /></div>
          <b>{d.n}</b>
        </div>
      ))}
      {!data.length && <p className="muted">No data yet</p>}
    </div>
  )
}

export function Empty({ title, hint, children }) {
  return (
    <div className="empty">
      <div className="empty-ring" />
      <h4>{title}</h4>
      {hint && <p>{hint}</p>}
      {children}
    </div>
  )
}

export function Ring({ value, size = 84 }) {
  const r = 34, c = 2 * Math.PI * r
  const v = Math.max(0, Math.min(100, value ?? 0))
  return (
    <svg width={size} height={size} viewBox="0 0 80 80">
      <circle cx="40" cy="40" r={r} fill="none" stroke="rgba(37,31,29,.1)" strokeWidth="6" />
      <circle cx="40" cy="40" r={r} fill="none" stroke="#fe5011" strokeWidth="6" strokeLinecap="round"
        strokeDasharray={`${(v / 100) * c} ${c}`} transform="rotate(-90 40 40)" style={{ transition: 'stroke-dasharray 1s' }} />
      <text x="40" y="46" textAnchor="middle" fill="#1e1d24" fontSize="22" fontWeight="300" fontFamily="Newsreader, Georgia, serif">{value ?? '–'}</text>
    </svg>
  )
}

// Brand colours for services whose logo we don't ship: a plain coloured tile with the first letter stands in for it.
const TILE = { twilio: '#F22F46', slack: '#4A154B', slack_webhook: '#4A154B', teams_webhook: '#5059C9', sendgrid: '#1A82E2', pipedrive: '#1B1B1B', microsoft365: '#D83B01', openweather: '#EB6E4B', tavily: '#3B5BFF', recall: '#111', custom_rest: '#555', books: '#7A5C3E' }
const EMOJI_ONLY = new Set(['weather', 'uk_postcodes', 'world_postcodes', 'ip_lookup', 'public_holidays', 'currency_rates', 'sun_times', 'uk_bank_holidays', 'books', 'countries', 'air_quality', 'vat_check', 'iban_check',
  'open_food_facts', 'crossref', 'openalex', 'uk_food_ratings', 'gov_uk', 'dictionary', 'word_finder', 'world_bank', 'jina_reader', 'europe_pmc', 'clinical_trials', 'open_fda', 'flood_warnings'])

// which logos exist (public/connector-icons/index.json), loaded once so apps without a logo don't cause failed image requests
let ICONS = null
const iconWaiters = []
function withIcons(cb) {
  if (ICONS) return cb(ICONS)
  iconWaiters.push(cb)
  if (iconWaiters.length > 1) return
  fetch('/connector-icons/index.json').then((r) => r.json()).catch(() => []).then((ids) => { ICONS = new Set(ids); iconWaiters.splice(0).forEach((f) => f(ICONS)) })
}
const TINTS = ['#4f6d5a', '#7c4a8b', '#c45423', '#12806f', '#3b5bff', '#8a6d3b', '#a1355b', '#2f6f9f']
const tintOf = (s) => TINTS[[...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % TINTS.length]

/** A connector's icon: its logo when we have one, otherwise a coloured tile with its first letter (or the emoji for the built-in helpers). */
export function AppIcon({ id, name = '', emoji = '🔌', size = 36 }) {
  const [has, setHas] = useState(ICONS ? ICONS.has(id) : null)
  useEffect(() => { let on = true; withIcons((set) => on && setHas(set.has(id))); return () => { on = false } }, [id])
  const box = { width: size, height: size, borderRadius: Math.round(size * 0.28), flex: 'none', display: 'grid', placeItems: 'center' }
  if (has && !EMOJI_ONLY.has(id) && !TILE[id]) {
    return <span style={{ ...box, background: 'rgba(255,255,255,.9)', border: '1px solid var(--line)' }}><img src={`/connector-icons/${id}.svg`} alt="" width={size * 0.6} height={size * 0.6} onError={() => setHas(false)} /></span>
  }
  if (EMOJI_ONLY.has(id)) return <span style={{ ...box, background: 'var(--soft)', fontSize: size * 0.55 }} aria-hidden>{emoji}</span>
  if (has === null) return <span style={{ ...box, background: 'var(--soft)' }} aria-hidden />
  return <span style={{ ...box, background: TILE[id] || tintOf(id), color: '#fff', fontWeight: 700, fontSize: size * 0.46 }} aria-hidden>{(name || id).trim().charAt(0).toUpperCase()}</span>
}

/** The same title row on every page: optional back link, title, one line of context, and the page's actions. */
export function PageHeader({ title, sub, actions, back, backTo }) {
  return (
    <>
      {back && <Link to={backTo || '/'} className="back"><Icon name="back" size={14} /> {back}</Link>}
      <header className="page-h">
        <div><h1>{title}</h1>{sub && <p className="muted">{sub}</p>}</div>
        {actions && <div className="actions">{actions}</div>}
      </header>
    </>
  )
}

export function Switch({ checked, onChange, label, disabled }) {
  return (
    <label className={`switch ${disabled ? 'off' : ''}`}>
      <input type="checkbox" role="switch" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <i aria-hidden="true" />{label && <span>{label}</span>}
    </label>
  )
}

export function Skeleton({ rows = 4, h = 44 }) {
  return <div className="skeleton" aria-busy="true">{Array.from({ length: rows }, (_, i) => <div key={i} style={{ height: h }} />)}</div>
}

export function EmptyState({ title, hint, action }) {
  return <div className="empty-state"><b>{title}</b>{hint && <p className="muted">{hint}</p>}{action}</div>
}

export function useTitle(t) {
  useEffect(() => { if (t) document.title = `${t} · Deskmates` }, [t])
}
