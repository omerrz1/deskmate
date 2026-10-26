import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, usePoll } from '../api'
import { onChange } from '../live'
import FileManager from '../FileManager'
import Icon from '../Icon'
import { Mail } from '../Mail'
import { Tasks } from '../pages/Office'
import './computer.css'
import { APPS, appOf, composeMs, sceneOf, sleep, typeMs } from './director'
import { ActivityApp, AppTile, BrowserApp, CalendarApp, ComposeSheet, NotesApp, PhoneApp, ProjectsApp, SceneBanner, Spotlight, TextApp } from './apps'

const STICKY = new Set(['text', 'web']) // scenes that stay on screen after they finish (the document, the open page)

/**
 * An employee's computer, filling the screen. One app at a time, switched from the list on the left. It shows what the employee
 * is doing: a cursor with their name moves to the app, opens it and types what they typed ("follow along").
 */
export default function Computer({ a, startFull = false }) {
  const id = a.id
  const root = useRef(null)
  const desk = useRef(null)
  const dock = useRef({})
  const [team] = usePoll('/agents', 0)
  const [status] = usePoll(`/agents/${id}/status`, 2500, [id])
  const [wins, setWins] = useState([{ k: 'files', x: 0, y: 0, w: 900, h: 520, z: 1, max: true }])
  const winsRef = useRef(wins)
  winsRef.current = wins
  const zref = useRef(1)
  const [follow, setFollow] = useState(true)
  const followRef = useRef(true)
  followRef.current = follow
  const [full, setFull] = useState(startFull)
  const [feed, setFeed] = useState([])
  const [toasts, setToasts] = useState([])
  const [scenes, setScenes] = useState({})
  const [openDoc, setOpenDoc] = useState(null)
  const [docTitle, setDocTitle] = useState('')
  const [cur, setCur] = useState({ x: 120, y: 160, dur: 0, show: false, click: 0 })
  const [bounce, setBounce] = useState(null)
  const [spot, setSpot] = useState(false)
  const [urlParams] = useSearchParams()
  const last = useRef(0)
  const first = useRef(true)
  const queue = useRef([])
  const playing = useRef(false)
  const hideTimer = useRef(null)
  const alive = useRef(true)
  const nextZ = () => { zref.current += 1; return zref.current }

  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { setFeed([]); last.current = 0; first.current = true; queue.current = []; setScenes({}) }, [id])
  useEffect(() => {
    const onFs = () => { if (!document.fullscreenElement) setFull(false) }
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])

  const size = () => { const r = desk.current?.getBoundingClientRect(); return { W: r?.width || 1000, H: r?.height || 600 } }
  const geometry = (app, n) => {
    const { W, H } = size()
    const w = Math.max(1, Math.min(Math.max(app.w, W * 0.5), W - 16)), h = Math.max(1, Math.min(Math.max(app.h, H * 0.62), H - 90))
    return { w, h, x: Math.max(8, Math.min(W - w - 8, 60 + (n % 6) * 40)), y: Math.max(8, Math.min(H - h - 90, 14 + (n % 6) * 30)) }
  }
  useEffect(() => { // when the screen gets bigger or smaller, windows follow
    const t = setTimeout(() => setWins((ws) => ws.map((w, n) => ({ ...w, ...geometry(appOf(w.k), n), max: w.max }))), 80)
    return () => clearTimeout(t)
  }, [full]) // eslint-disable-line
  useEffect(() => {
    const el = desk.current
    if (!el) return undefined
    const observer = new ResizeObserver(() => {
      const { W, H } = size()
      setWins(ws => ws.map(w => {
        const width = Math.max(1, Math.min(w.w, W - 16))
        const height = Math.max(1, Math.min(w.h, H - 90))
        return { ...w, w: width, h: height, x: Math.max(0, Math.min(w.x, W - width)), y: Math.max(0, Math.min(w.y, H - height - 70)) }
      }))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, []) // eslint-disable-line
  const toggleFull = () => {
    if (!full) { setFull(true); try { document.documentElement.requestFullscreen?.() } catch { /* ignore */ } }
    else { setFull(false); try { if (document.fullscreenElement) document.exitFullscreen() } catch { /* ignore */ } }
  }

  /* ---------- windows ---------- */
  const open = useCallback((k, launch = true) => {
    const app = appOf(k)
    if (!app) return
    const z = nextZ()
    setWins((ws) => {
      const ex = ws.find((w) => w.k === k)
      if (ex) return ws.map((w) => (w.k === k ? { ...w, min: false, anim: '', z } : w))
      return [...ws, { k, ...geometry(app, ws.length), z, max: true }]
    })
    if (launch) { setBounce(k); setTimeout(() => setBounce((b) => (b === k ? null : b)), 900) }
  }, []) // eslint-disable-line
  const focus = (k) => { const z = nextZ(); setWins((ws) => ws.map((w) => (w.k === k ? { ...w, z } : w))) }
  const patch = (k, p) => setWins((ws) => ws.map((w) => (w.k === k ? { ...w, ...p } : w)))
  useEffect(() => { const want = Number(urlParams.get('doc')); if (want) setTimeout(() => showDoc(want), 400) }, []) // eslint-disable-line
  const showDoc = (docId, title = '') => { setOpenDoc(docId); setDocTitle(title); setScenes((s) => ({ ...s, text: null })); open('text', false) }

  /* ---------- the employee's cursor ---------- */
  const point = (el) => { const r = el?.getBoundingClientRect(), b = root.current?.getBoundingClientRect(); return r && b ? { x: r.left - b.left + r.width / 2, y: r.top - b.top + r.height / 2 } : null }
  const moveTo = (p, dur = 800) => new Promise((res) => {
    if (!p) return res()
    clearTimeout(hideTimer.current)
    setCur((c) => ({ ...c, x: p.x, y: p.y, dur, show: true }))
    setTimeout(res, dur)
  })
  const click = async () => { setCur((c) => ({ ...c, click: c.click + 1 })); await sleep(170) }
  const inWindow = (k, fx, fy) => {
    const w = winsRef.current.find((x) => x.k === k), b = desk.current?.getBoundingClientRect(), r = root.current?.getBoundingClientRect()
    if (!w || !b || !r) return null
    return { x: b.left - r.left + (w.max ? b.width : w.w) * fx + (w.max ? 0 : w.x), y: b.top - r.top + (w.max ? b.height : w.h) * fy + (w.max ? 0 : w.y) }
  }
  const spot_ = { text: [0.5, 0.3], mail: [0.62, 0.3], web: [0.5, 0.12], calendar: [0.4, 0.4], phone: [0.5, 0.35], notes: [0.6, 0.3], tasks: [0.5, 0.3], files: [0.55, 0.4], projects: [0.5, 0.3], activity: [0.5, 0.5] }

  /* ---------- notifications ---------- */
  const pushNote = (app, title, text) => {
    const nid = Math.random().toString(36).slice(2)
    setToasts((t) => [...t.slice(-2), { nid, app, title, text }])
    setTimeout(() => alive.current && setToasts((t) => t.filter((x) => x.nid !== nid)), 6000)
  }

  /* ---------- playing what they did ---------- */
  const play = async (e, fast) => {
    const sc = sceneOf(e)
    if (!sc) return
    const app = appOf(sc.app)
    if (!followRef.current || !app) { pushNote(sc.app, app?.n || 'Deskmates', sc.note || e.summary); return }
    const key = `${e.id}-${Date.now()}`
    await moveTo(point(dock.current[sc.app]), fast ? 280 : 850)
    await click()
    open(sc.app)
    await sleep(fast ? 120 : 420)
    const [fx, fy] = spot_[sc.app] || [0.5, 0.4]
    await moveTo(inWindow(sc.app, fx, fy), fast ? 240 : 700)
    let ms = fast ? 600 : 1500
    if (sc.kind === 'type') ms = typeMs(sc.text, fast ? 900 : 4200)
    else if (sc.kind === 'sheet') ms = Math.min(3000, 600 + (sc.rows?.length || 0) * 160)
    else if (sc.kind === 'compose') ms = fast ? 900 : composeMs(sc)
    else if (sc.kind === 'browse' || sc.kind === 'search') ms = fast ? 900 : 2800
    else if (sc.kind === 'call') ms = 2600
    else if (sc.kind === 'note') ms = typeMs(sc.text, 2000) + 500
    if (sc.kind === 'compose') ms = fast ? 900 : composeMs(sc)
    setScenes((s) => ({ ...s, [sc.app]: { ...sc, key, eid: e.id, ms } }))
    await sleep(ms + 400)
    if (!STICKY.has(sc.app)) setScenes((s) => ({ ...s, [sc.app]: null }))
    pushNote(sc.app, app.n, sc.note || e.summary)
    if (sc.app === 'mail' || sc.app === 'tasks' || sc.app === 'files') { /* the real list underneath refreshes by itself */ }
  }
  const run = async () => {
    playing.current = true
    while (queue.current.length && alive.current) {
      const e = queue.current.shift()
      try { await play(e, queue.current.length > 2) } catch { /* a scene that fails must not stop the rest */ }
    }
    playing.current = false
    hideTimer.current = setTimeout(() => alive.current && setCur((c) => ({ ...c, show: false })), 7000)
  }
  const enqueue = (rows) => { queue.current.push(...rows); if (!playing.current) run() }

  // watch their history; every new entry is played on screen
  useEffect(() => {
    let live = true
    const tick = async () => {
      try {
        const rows = await api.get(`/agents/${id}/activity?after=${last.current}`)
        if (!live) return
        if (rows.length) {
          const fresh = rows.map((r) => ({ ...r, detail: typeof r.detail === 'string' ? safeJson(r.detail) : r.detail || {} }))  // already oldest first
          last.current = Math.max(...rows.map((r) => r.id))
          setFeed((f) => [...f, ...fresh].sort((x, y) => x.id - y.id).slice(-200))
          if (!first.current) enqueue(fresh)
        }
        first.current = false
      } catch { /* ignore */ }
    }
    tick()
    const t = setInterval(tick, 5000)  // a safety net: pushes from the server arrive at once
    const off = onChange((tables) => { if (tables.includes('agent_activity')) tick() })
    return () => { live = false; clearInterval(t); off() }
  }, [id]) // eslint-disable-line

  const replay = () => {
    const worth = feed.filter((e) => sceneOf(e)).slice(-3)
    if (worth.length && !playing.current) enqueue(worth.map((e) => ({ ...e, id: e.id })))
  }

  /* ---------- keyboard ---------- */
  useEffect(() => {
    if (!full) return undefined
    const k = (e) => { if (e.key === 'Escape' && !spot) setFull(false) }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [full, spot])

  const sorted = wins.filter((w) => !w.min).sort((x, y) => y.z - x.z)
  const focused = sorted[0]
  const working = status?.working
  const away = !!status && !working && !status.in_meeting && (status.idle_for || 0) > 3600
  const state = working ? `Working on: ${status.task}` : status?.in_meeting ? 'In a meeting' : away ? 'Away from their desk' : 'Free right now'
  const hasScene = useMemo(() => feed.some((e) => sceneOf(e)), [feed])
  const body = (k) => {
    const s = scenes[k]
    switch (k) {
      case 'files': return <FileManager fill agentId={id} owner={String(id)} />
      case 'mail': return <div className="ox-fill"><Mail team={team || []} fixedBox={id} /><ComposeSheet scene={s?.kind === 'compose' ? s : null} /></div>
      case 'tasks': return <div className="ox-fill ox-pad"><Tasks team={team || []} initialAgent={String(id)} fixed /><SceneBanner scene={s} /></div>
      case 'calendar': return <CalendarApp id={id} scene={s} />
      case 'text': return <TextApp scene={s} openDoc={openDoc} name={a.name} />
      case 'web': return <BrowserApp feed={feed} scene={s} />
      case 'phone': return <PhoneApp id={id} scene={s} />
      case 'notes': return <NotesApp id={id} scene={s} />
      case 'projects': return <div className="ox-fill"><ProjectsApp id={id} /><SceneBanner scene={s} /></div>
      case 'activity': return <ActivityApp feed={feed} name={a.name} />
      default: return null
    }
  }
  const titleOf = (w) => (w.k === 'text' ? scenes.text?.title || (openDoc ? docTitle || 'Document' : 'Documents') : appOf(w.k).n)

  return (
    <div className={`ox ${full ? 'full' : ''}`} style={{ '--c': a.color || '#7c4a8b' }} ref={root} tabIndex={0}
      onKeyDown={(e) => { if (e.key === '/' && !/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) { e.preventDefault(); setSpot(true) } }}>

      <div className="ox-bar" onPointerDown={(e) => e.stopPropagation()}>
        <span className="ox-state" title={state}><i className={`dot ${working ? 'live' : status?.in_meeting ? 'ok' : ''}`} /><b>{a.name}</b><span>{state}</span></span>
        <span className="grow" />
        <label className="ox-follow" title="Follow along: opens the app they are using and shows where they click"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /><span>Follow along</span></label>
        {hasScene && <button className="ox-act" onClick={replay} title="Watch what they just did again"><Icon name="play" size={13} /><span>Replay</span></button>}
        <button className="ox-act" onClick={() => setSpot(true)} title="Search this computer (or press /)"><Icon name="search" size={14} /><span>Search</span></button>
        <button className="ox-act" onClick={toggleFull} title={full ? 'Leave full screen' : 'Use the whole screen'}><Icon name={full ? 'shrink' : 'expand'} size={14} /><span>{full ? 'Leave full screen' : 'Full screen'}</span></button>
      </div>

      <div className="ox-body">
        <nav className="ox-apps" aria-label="Apps" onPointerDown={(e) => e.stopPropagation()}>
          {APPS.map((x) => (
            <button key={x.k} className={`ox-app ${focused?.k === x.k ? 'on' : ''}`} onClick={() => open(x.k, true)} title={x.n}>
              <span className={`ox-ico ${bounce === x.k ? 'bounce' : ''}`} ref={(el) => { dock.current[x.k] = el }}><AppTile k={x.k} size={34} /></span>
              <span className="lb">{x.n}</span>
            </button>
          ))}
        </nav>

        <div className="ox-desk" ref={desk}>
          {wins.map((w) => {
            const on = focused?.k === w.k
            return (
              <div key={w.k} className={`ox-win max ${on ? 'focus' : ''}`} style={{ zIndex: w.z, display: w.min || !on ? 'none' : 'flex' }} onPointerDown={() => focus(w.k)} aria-label={titleOf(w)}>
                <div className="ox-wbody">{body(w.k)}</div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="ox-notes" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.nid} className="ox-note"><AppTile k={t.app} size={34} /><div><b>{t.title}</b><p>{t.text}</p></div><small>now</small></div>
        ))}
      </div>

      <div className={`ox-cur ${cur.show ? 'show' : ''}`} style={{ transform: `translate(${cur.x}px, ${cur.y}px)`, '--dur': `${cur.dur}ms` }} aria-hidden>
        <svg width="20" height="24" viewBox="0 0 20 24"><path d="M2 1.5l15 11.2-6.6 1.1 3.9 7.2-2.9 1.6-3.9-7.2L2.5 20z" /></svg>
        <label style={{ background: a.color || '#7c4a8b' }}>{a.name}</label>
        {cur.click > 0 && <i className="ox-rip" key={cur.click} />}
      </div>

      {spot && <Spotlight id={id} onClose={() => setSpot(false)} onApp={(k) => open(k)} onDoc={showDoc} />}
    </div>
  )
}

function safeJson(s) { try { return JSON.parse(s) } catch { return {} } }
