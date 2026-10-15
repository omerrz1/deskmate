import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, usePoll } from '../api'
import Icon from '../Icon'
import { APPS, appOf, shortUrl, typeMs, useTypewriter } from './director'

const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '')
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

/** A macOS-style app icon: a gradient tile with the app's glyph. */
export function AppTile({ k, size = 46 }) {
  const app = appOf(k) || APPS[0]
  const light = k === 'calendar'
  return (
    <span className="ox-tile" style={{ width: size, height: size, background: `linear-gradient(160deg, ${app.g[0]}, ${app.g[1]})`, color: light ? '#e5383b' : '#fff' }}>
      {light
        ? <span className="ox-cal-tile"><i>{new Date().toLocaleDateString([], { weekday: 'short' }).toUpperCase()}</i><b style={{ fontSize: size * 0.46 }}>{new Date().getDate()}</b></span>
        : <Icon name={app.i} size={Math.round(size * 0.5)} />}
    </span>
  )
}

/** A short line typed out inside a window, for scenes that have no window of their own (a new to-do, a calendar entry…). */
export function SceneBanner({ scene }) {
  const label = { todo: 'New to-do', 'todo-done': 'Done', event: 'New event', note: 'New note', poke: '' }[scene?.kind] ?? ''
  const text = scene?.title || scene?.text || scene?.note || ''
  const typed = useTypewriter(text, Math.min(1400, typeMs(text, 1400)), !!scene)
  if (!scene) return null
  return (
    <div className="ox-banner" key={scene.key}>
      {scene.kind === 'todo-done' ? <Icon name="check" size={15} /> : <Icon name="plus" size={15} />}
      {label && <b>{label}</b>}<span>{typed}<i className="ox-caret" /></span>
    </div>
  )
}


/** Documents the team writes use light markdown (# headings, **bold**, - lists): show them the way a text editor would. */
function Md({ text }) {
  const inline = (t) => String(t).split(/(\*\*[^*]+\*\*)/g).map((p, i) => (/^\*\*[^*]+\*\*$/.test(p) ? <b key={i}>{p.slice(2, -2)}</b> : p))
  return String(text || '').split('\n').map((l, i) => {
    const h = l.match(/^(#{1,4})\s+(.*)$/)
    if (h) return <div key={i} className={`h h${h[1].length}`}>{inline(h[2])}</div>
    const li = l.match(/^\s*[-*•]\s+(.*)$/)
    if (li) return <div key={i} className="li"><span>•</span><span>{inline(li[1])}</span></div>
    return <div key={i} className="p">{l.trim() ? inline(l) : '\u00a0'}</div>
  })
}

/* ---------------- Documents (a text editor that can be watched typing) ---------------- */
export function TextApp({ scene, openDoc, name }) {
  const [docRaw] = usePoll(openDoc ? `/docs/${openDoc}` : '/status', 0, [openDoc])
  const doc = openDoc && docRaw?.id === openDoc ? docRaw : null
  const live = scene && (scene.kind === 'type' || scene.kind === 'sheet')
  const ms = scene?.ms || 2000
  const typed = useTypewriter(scene?.text, ms, live && scene.kind === 'type')
  const [saved, setSaved] = useState(false)
  useEffect(() => { setSaved(false); if (!live) return undefined; const t = setTimeout(() => setSaved(true), ms + 300); return () => clearTimeout(t) }, [scene?.key]) // eslint-disable-line
  const words = (live ? typed : doc?.body || '').trim().split(/\s+/).filter(Boolean).length

  if (live && scene.kind === 'sheet') return <SheetScene scene={scene} saved={saved} />
  const title = live ? scene.title : doc?.title
  const body = live ? typed : doc?.kind === 'sheet' ? null : doc?.body
  if (!live && !doc) return <div className="ox-empty"><Icon name="doc" size={34} /><b>No document open</b><span>Open a file from the desktop, or watch {name} write one.</span></div>
  return (
    <div className="ox-text">
      <div className="ox-tbar"><span className="fmt"><b>B</b><i>I</i><u>U</u></span><span className="sep" /><span className="sel">Helvetica Neue</span><span className="sel">13</span><span className="grow" /><span className="ox-saved">{live ? (saved ? 'Saved' : 'Saving…') : 'Saved'}</span></div>
      <div className="ox-paper-wrap">
        <article className="ox-paper">
          <h1>{title}</h1>
          {doc?.kind === 'sheet' && !live ? <SheetTable table={doc.table} /> : <div className="ox-ptext"><Md text={body} />{live && !saved && <i className="ox-caret dark" />}</div>}
        </article>
      </div>
      <div className="ox-status"><span>{words} word{words === 1 ? '' : 's'}</span><span>{live ? (saved ? 'All changes saved' : 'Editing…') : 'All changes saved'}</span></div>
    </div>
  )
}
function SheetTable({ table }) {
  const cols = table?.columns || [], rows = table?.rows || []
  return (
    <div className="ox-sheet"><table><thead><tr><th className="rn" />{cols.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
      <tbody>{rows.slice(0, 60).map((r, i) => <tr key={i}><td className="rn">{i + 1}</td>{cols.map((_, j) => <td key={j}>{r[j]}</td>)}</tr>)}</tbody></table></div>
  )
}
function SheetScene({ scene, saved }) {
  const cols = scene.columns.length ? scene.columns : ['A', 'B', 'C']
  const rows = scene.rows.map((r) => (Array.isArray(r) ? r : Object.values(r || {})))
  const [shown, setShown] = useState(0)
  useEffect(() => {
    setShown(0)
    const per = Math.max(60, Math.min(260, 2400 / Math.max(1, rows.length)))
    const t = setInterval(() => setShown((n) => (n >= rows.length ? n : n + 1)), per)
    return () => clearInterval(t)
  }, [scene.key]) // eslint-disable-line
  return (
    <div className="ox-text">
      <div className="ox-tbar"><span className="sel">{scene.title}</span><span className="grow" /><span className="ox-saved">{saved ? 'Saved' : 'Saving…'}</span></div>
      <div className="ox-paper-wrap flat"><SheetTable table={{ columns: cols, rows: rows.slice(0, shown) }} /></div>
      <div className="ox-status"><span>{rows.length} row{rows.length === 1 ? '' : 's'}</span><span>{saved ? 'All changes saved' : 'Editing…'}</span></div>
    </div>
  )
}

/* ---------------- Mail: a new message being written, on top of the real mailbox ---------------- */
export function ComposeSheet({ scene }) {
  const [stage, setStage] = useState(0)
  const body = scene?.text || ''
  useEffect(() => {
    if (!scene) return undefined
    setStage(0)
    const t = [setTimeout(() => setStage(1), 650), setTimeout(() => setStage(2), 1500), setTimeout(() => setStage(3), 1500 + typeMs(body, 3600) + 150), setTimeout(() => setStage(4), 1500 + typeMs(body, 3600) + 700)]
    return () => t.forEach(clearTimeout)
  }, [scene?.key]) // eslint-disable-line
  const to = useTypewriter(scene?.to, 600, stage >= 0)
  const subject = useTypewriter(scene?.subject, 700, stage >= 1)
  const text = useTypewriter(body, typeMs(body, 3600), stage >= 2)
  if (!scene) return null
  return (
    <div className={`ox-compose ${stage >= 4 ? 'gone' : ''}`}>
      <header><span className="dots"><i /><i /><i /></span><b>{scene.subject || 'New message'}</b><span className="grow" />
        <button className={`ox-send ${stage === 3 ? 'press' : ''}`} tabIndex={-1}>{stage >= 4 ? 'Sent ✓' : 'Send'}</button></header>
      <div className="row"><span>To:</span><span>{stage >= 1 ? scene.to : to}{stage === 0 && <i className="ox-caret dark" />}</span></div>
      <div className="row"><span>Subject:</span><span>{stage >= 2 ? scene.subject : subject}{stage === 1 && <i className="ox-caret dark" />}</span></div>
      <div className="ox-cbody">{stage >= 3 ? body : text}{stage === 2 && <i className="ox-caret dark" />}</div>
    </div>
  )
}

/* ---------------- Browser ---------------- */
export function BrowserApp({ feed, scene }) {
  const pages = useMemo(() => feed.filter((e) => (['read_webpage', 'learn_from_website', 'web_search', 'news_search'].includes(e.tool) || (e.tool.startsWith('browser_') && e.detail?.shot)) && e.id !== scene?.eid).map((e) => {
    const d = e.detail || {}, a = d.args || {}
    if (e.tool.startsWith('browser_')) return { id: e.id, kind: 'shot', title: d.title || shortUrl(d.url), url: d.url || '', shot: d.shot, at: d.at, ts: e.ts }
    return e.tool.includes('search') ? { id: e.id, kind: 'search', title: `${a.query || 'Search'} - Search`, url: `search://${a.query || ''}`, query: a.query, results: d.results || [], ts: e.ts }
      : { id: e.id, kind: 'page', title: d.title || shortUrl(d.url), url: d.url || a.url || '', text: d.snippet, ts: e.ts }
  }).slice(-6), [feed, scene?.eid])
  const [active, setActive] = useState(null)
  const live = scene && (scene.kind === 'browse' || scene.kind === 'search' || scene.kind === 'shot')
  useEffect(() => { if (live) setActive('live') }, [scene?.key]) // eslint-disable-line
  const cur = live && active === 'live' ? { id: 'live', kind: scene.kind === 'search' ? 'search' : scene.kind === 'shot' ? 'shot' : 'page', shot: scene.shot, at: scene.at, title: scene.title || scene.query || shortUrl(scene.url), url: scene.kind === 'search' ? `search://${scene.query}` : scene.url, text: scene.text, query: scene.query, results: scene.results } : pages.find((p) => p.id === active) || pages[pages.length - 1]
  const addr = cur ? (cur.kind === 'search' ? `Search the web: ${cur.query}` : cur.url) : ''
  const typed = useTypewriter(addr, 900, live && active === 'live')
  const [loaded, setLoaded] = useState(true)
  useEffect(() => { if (!live) return undefined; setLoaded(false); const t = setTimeout(() => setLoaded(true), 1500); return () => clearTimeout(t) }, [scene?.key]) // eslint-disable-line
  const showTyped = live && active === 'live' && typed.length < addr.length
  return (
    <div className="ox-browser">
      <div className="ox-tabs">
        {pages.map((p) => <button key={p.id} className={`tab ${cur?.id === p.id ? 'on' : ''}`} onClick={() => setActive(p.id)}><span className="fav">{(p.kind === 'search' ? 'S' : shortUrl(p.url).slice(0, 1) || '·').toUpperCase()}</span><span>{p.title}</span></button>)}
        {live && <button className={`tab ${active === 'live' ? 'on' : ''}`} onClick={() => setActive('live')}><span className="fav">{(scene.kind === 'search' ? 'S' : shortUrl(scene.url).slice(0, 1)).toUpperCase()}</span><span>{!loaded ? 'Loading…' : cur?.title}</span></button>}
      </div>
      <div className="ox-addr"><span className="nav">‹</span><span className="nav">›</span><span className="nav">⟳</span>
        <div className="pill"><Icon name="link" size={12} />{addr ? (showTyped ? typed : addr) : <em>Search or enter website name</em>}{showTyped && <i className="ox-caret dark" />}</div></div>
      <div className={`ox-load ${live && active === 'live' && !loaded ? 'on' : ''}`} />
      <div className="ox-page">
        {!cur ? <div className="ox-empty"><Icon name="globe" size={34} /><b>Nothing open</b><span>Pages and searches appear here as they use the web.</span></div>
          : live && active === 'live' && !loaded ? null
            : cur.kind === 'shot' ? (
              <div className="ox-shot"><img src={cur.shot} alt={`Screenshot of ${cur.title || cur.url}`} />{cur.at && <i className="ox-ring" key={cur.id} style={{ left: `${cur.at.x * 100}%`, top: `${cur.at.y * 100}%` }} />}</div>
            ) : cur.kind === 'search' ? (
              <div className="ox-serp"><h2>Results for “{cur.query}”</h2>{(cur.results || []).length === 0 && <p className="muted small">No results were kept for this search.</p>}
                {(cur.results || []).map((r, i) => <div key={i} className="res" style={{ animationDelay: `${i * 90}ms` }}><small>{shortUrl(r.url)}</small><b>{r.title}</b><p>{r.snippet}</p></div>)}</div>
            ) : (
              <article className="ox-article"><small>{shortUrl(cur.url)}</small><h1>{cur.title}</h1><p>{cur.text || 'The page text was not kept.'}</p></article>
            )}
      </div>
    </div>
  )
}

/* ---------------- Calendar ---------------- */
export function CalendarApp({ id, scene }) {
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1) })
  const [ev, reload] = usePoll(`/events?days=120&back=60&agent_id=${id}`, 8000, [id])
  const [sel, setSel] = useState(() => new Date().toDateString())
  const typed = useTypewriter(scene?.kind === 'event' ? scene.title : '', 900, scene?.kind === 'event')
  useEffect(() => { if (scene?.kind !== 'event') return undefined; const t = setTimeout(reload, 1500); return () => clearTimeout(t) }, [scene?.key]) // eslint-disable-line
  const first = month.getDay() === 0 ? 6 : month.getDay() - 1  // weeks start on Monday
  const days = Array.from({ length: 42 }, (_, i) => new Date(month.getFullYear(), month.getMonth(), 1 - first + i))
  const byDay = {}
  ;(ev || []).forEach((e) => { const k = new Date(e.start * 1000).toDateString(); (byDay[k] = byDay[k] || []).push(e) })
  const today = new Date().toDateString()
  const up = (ev || []).filter((e) => e.start * 1000 > Date.now()).slice(0, 6)
  return (
    <div className="ox-cal">
      <div className="head">
        <h2>{month.toLocaleDateString([], { month: 'long', year: 'numeric' })}</h2><span className="grow" />
        <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} aria-label="Previous month">‹</button>
        <button onClick={() => { const d = new Date(); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); setSel(d.toDateString()) }}>Today</button>
        <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} aria-label="Next month">›</button>
      </div>
      <div className="body">
        <div className="grid">
          {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => <div key={d} className="dow">{d}</div>)}
          {days.map((d) => {
            const k = d.toDateString(), list = byDay[k] || []
            return (
              <button key={k} className={`cell ${d.getMonth() !== month.getMonth() ? 'out' : ''} ${k === today ? 'today' : ''} ${k === sel ? 'sel' : ''}`} onClick={() => setSel(k)}>
                <span className="n">{d.getDate()}</span>
                {list.slice(0, 3).map((e) => <span key={e.id} className="chip" title={e.title}>{e.title}</span>)}
                {list.length > 3 && <span className="more">+{list.length - 3} more</span>}
              </button>
            )
          })}
        </div>
        <aside className="side">
          <h4>{sel === today ? 'Today' : new Date(sel).toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'short' })}</h4>
          {(byDay[sel] || []).length === 0 && <p className="faint small">Nothing planned.</p>}
          {(byDay[sel] || []).map((e) => <div key={e.id} className="ev"><b>{hhmm(e.start)}</b><span>{e.title}{e.location ? <small> · {e.location}</small> : null}</span></div>)}
          <h4 style={{ marginTop: 18 }}>Coming up</h4>
          {up.length === 0 && <p className="faint small">Nothing coming up.</p>}
          {up.map((e) => <div key={e.id} className="ev"><b>{new Date(e.start * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' })}</b><span>{e.title}</span></div>)}
        </aside>
      </div>
      {scene?.kind === 'event' && <div className="ox-pop" key={scene.key}><small>New event</small><b>{typed}<i className="ox-caret dark" /></b>{scene.start && <span>{String(scene.start).replace('T', ' ').slice(0, 16)}</span>}</div>}
    </div>
  )
}

/* ---------------- Notes ---------------- */
export function NotesApp({ id, scene }) {
  const [a] = usePoll(`/agents/${id}`, 6000, [id])
  const [skills] = usePoll(`/skills?agent_id=${id}`, 9000, [id])
  const [sel, setSel] = useState(null)
  const typed = useTypewriter(scene?.kind === 'note' ? scene.text : '', typeMs(scene?.text, 2000), scene?.kind === 'note')
  const notes = a?.notes || []
  const items = [...notes.map((n) => ({ k: `n${n.id}`, title: String(n.note).split('\n')[0].slice(0, 48), body: n.note, tag: 'Note' })), ...(skills || []).map((s) => ({ k: `s${s.id}`, title: s.name, body: s.steps, tag: 'Procedure' }))]
  const cur = items.find((i) => i.k === sel) || items[0]
  return (
    <div className="ox-notes-app">
      <aside>
        {scene?.kind === 'note' && <div className="item new on" key={scene.key}><b>{typed.split('\n')[0] || 'New note'}</b><small>just now</small></div>}
        {items.length === 0 && !scene && <p className="faint small" style={{ padding: 14 }}>No notes yet.</p>}
        {items.map((i) => <button key={i.k} className={`item ${cur?.k === i.k && !(scene?.kind === 'note') ? 'on' : ''}`} onClick={() => setSel(i.k)}><b>{i.title}</b><small>{i.tag}</small></button>)}
      </aside>
      <section>{scene?.kind === 'note' ? <div className="body"><pre>{typed}<i className="ox-caret dark" /></pre></div> : cur ? <div className="body"><h3>{cur.title}</h3><pre>{cur.body}</pre></div> : <div className="ox-empty"><Icon name="note" size={30} /><b>No note selected</b></div>}</section>
    </div>
  )
}

/* ---------------- Calls ---------------- */
export function PhoneApp({ id, scene }) {
  const [calls] = usePoll(`/agents/${id}/calls`, 5000, [id])
  const [sec, setSec] = useState(0)
  useEffect(() => { setSec(0); if (scene?.kind !== 'call') return undefined; const t = setInterval(() => setSec((s) => s + 1), 1000); return () => clearInterval(t) }, [scene?.key]) // eslint-disable-line
  return (
    <div className="ox-phone">
      <h2>Recents</h2>
      {(calls || []).length === 0 && <p className="faint small" style={{ padding: '0 18px' }}>No calls yet. Calls for this employee's campaigns, and ones they place, show up here.</p>}
      <ul>{(calls || []).map((c) => (
        <li key={c.id}><Link to={`/calls?open=${c.id}`}><span className="av">{(c.contact || 'C').slice(0, 1).toUpperCase()}</span>
          <span className="m"><b>{c.contact || 'Customer'}</b><small>{c.summary || c.outcome || c.status}</small></span><span className="r"><small>{when(c.created_at)}</small><small>{Math.round(c.duration || 0)}s</small></span></Link></li>
      ))}</ul>
      {scene?.kind === 'call' && (
        <div className="ox-dial" key={scene.key}><span className="avwrap"><span className="ring"><i /><i /></span><span className="av lg"><Icon name="phone" size={26} /></span></span><b>Calling…</b><small>{scene.title || 'A customer'}</small><span className="t">{String(Math.floor(sec / 60)).padStart(2, '0')}:{String(sec % 60).padStart(2, '0')}</span></div>
      )}
    </div>
  )
}

/* ---------------- Projects ---------------- */
export function ProjectsApp({ id }) {
  const [ps] = usePoll('/projects', 6000)
  const mine = (ps || []).filter((p) => (p.members || []).some((m) => m.id === id))
  return (
    <div className="ox-projects">
      {mine.length === 0 && <div className="ox-empty"><Icon name="projects" size={32} /><b>Not in any project yet</b></div>}
      {mine.map((p) => (
        <Link key={p.id} to={`/projects/${p.id}`} className="card"><div><b>{p.name}</b><small>{p.goal}</small></div>
          <div className="bar"><i style={{ width: `${p.progress || 0}%` }} /></div><span>{p.progress || 0}% done</span></Link>
      ))}
    </div>
  )
}

/* ---------------- Activity monitor ---------------- */
export function ActivityApp({ feed, name }) {
  const ref = useRef(null)
  useEffect(() => { ref.current?.scrollTo({ top: 1e9 }) }, [feed.length])
  const bins = useMemo(() => { // activity per minute for the last 30 minutes
    const now = Date.now() / 1000, out = Array(30).fill(0)
    feed.forEach((e) => { const m = Math.floor((now - e.ts) / 60); if (m >= 0 && m < 30) out[29 - m] += 1 })
    return out
  }, [feed])
  const max = Math.max(1, ...bins)
  return (
    <div className="ox-actmon">
      <div className="graph"><div className="legend"><b>{name}</b><small>actions per minute, last 30 minutes</small></div>
        <svg viewBox="0 0 300 54" preserveAspectRatio="none">{bins.map((n, i) => <rect key={i} x={i * 10 + 1} width="8" y={54 - (n / max) * 50 - 2} height={(n / max) * 50 + 2} rx="1.5" className={n ? 'on' : ''} />)}</svg></div>
      <div className="log" ref={ref}>
        {feed.length === 0 && <p className="faint small" style={{ padding: 14 }}>Nothing yet.</p>}
        {feed.map((e) => (
          <div key={e.id} className={`row ${e.ok ? '' : 'bad'}`}><time>{new Date(e.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time><AppTile k={appOf(e.app) ? e.app : 'activity'} size={18} /><span>{e.summary}</span></div>
        ))}
      </div>
    </div>
  )
}

/* ---------------- Spotlight ---------------- */
export function Spotlight({ id, onClose, onApp, onDoc }) {
  const [q, setQ] = useState('')
  const [at, setAt] = useState(0)
  const [files, setFiles] = useState([])
  const input = useRef(null)
  useEffect(() => { input.current?.focus() }, [])
  useEffect(() => {
    if (q.trim().length < 2) { setFiles([]); return undefined }
    const t = setTimeout(() => api.get(`/fs/search?owner=${id}&q=${encodeURIComponent(q.trim())}`).then((r) => setFiles((r.files || []).slice(0, 5))).catch(() => setFiles([])), 200)
    return () => clearTimeout(t)
  }, [q, id])
  const apps = q.trim() ? APPS.filter((a) => a.n.toLowerCase().includes(q.trim().toLowerCase())) : APPS.slice(0, 6)
  const rows = [...apps.map((a) => ({ g: 'Applications', t: a.n, k: a.k, run: () => onApp(a.k) })), ...files.map((f) => ({ g: 'Documents', t: f.title, sub: f.path || 'On this computer', icon: f.kind === 'sheet' ? 'sheet' : 'doc', run: () => onDoc(f.id, f.title) }))]
  const key = (e) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') { e.preventDefault(); setAt((x) => Math.min(rows.length - 1, x + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setAt((x) => Math.max(0, x - 1)) }
    else if (e.key === 'Enter') { rows[at]?.run(); onClose() }
  }
  let last = ''
  return (
    <div className="ox-spot-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ox-spot" onKeyDown={key}>
        <div className="in"><Icon name="search" size={18} /><input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setAt(0) }} placeholder="Spotlight Search" /></div>
        {rows.length > 0 && <ul>{rows.map((r, n) => { const head = r.g !== last ? <li className="g">{r.g}</li> : null; last = r.g; return (
          <Fragment key={`${r.g}-${r.t}-${n}`}>{head}<li className={n === at ? 'on' : ''} onMouseMove={() => setAt(n)} onClick={() => { r.run(); onClose() }}>{r.k ? <AppTile k={r.k} size={24} /> : <span className="fi"><Icon name={r.icon} size={14} /></span>}<span>{r.t}</span>{r.sub && <small>{r.sub}</small>}</li></Fragment>
        ) })}</ul>}
      </div>
    </div>
  )
}
