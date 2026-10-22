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

const BAR = 28 // height of the menu bar
const STICKY = new Set(['text', 'web']) // scenes that stay on screen after they finish (the document, the open page)

/**
 * A whole computer on a big screen. A real-looking desktop: menu bar, dock, windows you can drag, resize and minimise,
 * Spotlight search, notifications, and a lock screen. It shows what the employee is doing: a cursor with their name moves to
 * the app, opens it, and types what they typed ("follow along").
 */
export default function Computer({ a, startFull = false }) {
  const id = a.id
  const root = useRef(null)
  const desk = useRef(null)
  const dock = useRef({})
  const [team] = usePoll('/agents', 0)
  const [status] = usePoll(`/agents/${id}/status`, 2500, [id])
  const [recent] = usePoll(`/agents/${id}/recent-files?limit=6`, 9000, [id])
  const [wins, setWins] = useState([{ k: 'files', x: 90, y: 24, w: 900, h: 520, z: 1 }])
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
  const [now, setNow] = useState(new Date())
  const bootKey = `ox-boot-${id}`
  const [booting, setBooting] = useState(() => { try { return !sessionStorage.getItem(bootKey) } catch { return false } })
  const [woke, setWoke] = useState(() => { try { return !!sessionStorage.getItem(`ox-woke-${id}`) } catch { return false } })
  const [urlParams] = useSearchParams()
  const last = useRef(0)
  const first = useRef(true)
  const queue = useRef([])
  const playing = useRef(false)
  const hideTimer = useRef(null)
  const alive = useRef(true)
  const nextZ = () => { zref.current += 1; return zref.current }

  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 15000); return () => clearInterval(t) }, [])
  useEffect(() => {
    if (!booting) return undefined
    const t = setTimeout(() => { setBooting(false); try { sessionStorage.setItem(bootKey, '1') } catch { /* private mode */ } }, 1900)
    return () => clearTimeout(t)
  }, [booting, bootKey])
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
      return [...ws, { k, ...geometry(app, ws.length), z }]
    })
    if (launch) { setBounce(k); setTimeout(() => setBounce((b) => (b === k ? null : b)), 900) }
  }, []) // eslint-disable-line
  const focus = (k) => { const z = nextZ(); setWins((ws) => ws.map((w) => (w.k === k ? { ...w, z } : w))) }
  const close = (k) => setWins((ws) => ws.filter((w) => w.k !== k))
  const patch = (k, p) => setWins((ws) => ws.map((w) => (w.k === k ? { ...w, ...p } : w)))
  const minimise = (k) => { patch(k, { anim: 'min' }); setTimeout(() => patch(k, { min: true, anim: '' }), 260) }
  const drag = (e, k, mode) => {
    if (e.button !== 0) return
    e.preventDefault(); e.stopPropagation()
    focus(k)
    const w = winsRef.current.find((x) => x.k === k)
    if (!w || w.max) return
    const sx = e.clientX, sy = e.clientY, { W, H } = size()
    const move = (ev) => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy
      if (mode === 'move') patch(k, { x: Math.max(0, Math.min(W - w.w, w.x + dx)), y: Math.max(0, Math.min(H - w.h - 70, w.y + dy)) })
      else patch(k, { w: Math.min(W - w.x, Math.max(240, w.w + dx)), h: Math.min(H - w.y - 70, Math.max(180, w.h + dy)) })
    }
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
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

  /* ---------- dock magnification ---------- */
  const magnify = (e) => {
    const x = e.clientX
    Object.values(dock.current).forEach((el) => {
      if (!el) return
      const r = el.getBoundingClientRect(), d = Math.abs(x - (r.left + r.width / 2))
      el.style.transform = `scale(${1 + 0.55 * Math.exp(-(d * d) / (2 * 55 * 55))})`
    })
  }
  const unmagnify = () => Object.values(dock.current).forEach((el) => { if (el) el.style.transform = '' })

  const sorted = wins.filter((w) => !w.min).sort((x, y) => y.z - x.z)
  const focused = sorted[0]
  const working = status?.working
  const state = working ? `Working on: ${status.task}` : status?.in_meeting ? 'In a meeting' : 'At their desk'
  const asleep = status && !working && !status.in_meeting && (status.idle_for || 0) > 3600 && !woke
  const hasScene = useMemo(() => feed.some((e) => sceneOf(e)), [feed])
  const lights = (k) => (
    <span className="ox-lights" onPointerDown={(e) => e.stopPropagation()}>
      <button className="r" onClick={() => close(k)} aria-label="Close"><svg viewBox="0 0 10 10"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg></button>
      <button className="y" onClick={() => minimise(k)} aria-label="Minimise"><svg viewBox="0 0 10 10"><path d="M2 5h6" /></svg></button>
      <button className="g" onClick={() => patch(k, { max: !winsRef.current.find((w) => w.k === k)?.max })} aria-label="Zoom"><svg viewBox="0 0 10 10"><path d="M2.5 7.5l5-5M3 2.5h4.5V7" /></svg></button>
    </span>
  )
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
  const clock = `${now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })}  ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`

  return (
    <div className={`ox ${full ? 'full' : ''}`} style={{ '--c': a.color || '#7c4a8b' }} ref={root} tabIndex={0}
      onKeyDown={(e) => { if (e.key === '/' && !/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) { e.preventDefault(); setSpot(true) } }}>
      <div className="ox-wall" />

      <div className="ox-menu" onPointerDown={(e) => e.stopPropagation()}>
        <span className="logo"><i /></span>
        <b className="app">{focused ? appOf(focused.k).n : 'Desktop'}</b>
        {['File', 'Edit', 'View', 'Window', 'Help'].map((m) => <span key={m} className="m">{m}</span>)}
        <span className="grow" />
        <span className="status" title={state}>{working && <i className="dot live" />}{state}</span>
        <label className="watch" title="Open the app they use, and show their cursor"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /><span>Watching</span></label>
        {hasScene && <button onClick={replay} title="Replay what they just did" aria-label="Replay what they just did"><Icon name="play" size={12} /></button>}
        <svg className="ico" viewBox="0 0 24 24" aria-hidden><path d="M2 9a15 15 0 0120 0M5 12.5a10.5 10.5 0 0114 0M8.5 16a5.5 5.5 0 017 0" /><circle cx="12" cy="19" r="1.2" /></svg>
        <svg className="ico bat" viewBox="0 0 28 14" aria-hidden><rect x="1" y="1" width="22" height="12" rx="3.2" /><rect x="3" y="3" width="15" height="8" rx="1.6" className="lvl" /><path d="M25 5v4" /></svg>
        <button onClick={() => setSpot(true)} title="Spotlight search (or press /)" aria-label="Search"><Icon name="search" size={13} /></button>
        <span className="clock">{clock}</span>
        <button onClick={toggleFull} title={full ? 'Exit full screen' : 'Full screen'} aria-label="Full screen"><Icon name={full ? 'shrink' : 'expand'} size={13} /></button>
      </div>

      <div className="ox-desk" ref={desk} style={{ top: BAR }} onPointerDown={(e) => { if (e.target === e.currentTarget) setOpenDoc((d) => d) }}>
        <div className="ox-icons">
          {(recent || []).map((f) => (
            <button key={f.id} className="ox-file" onDoubleClick={() => showDoc(f.id, f.title)} onClick={(e) => e.currentTarget.focus()} title={`${f.title}${f.path ? ` · ${f.path}` : ''}`}>
              <span className={`pg ${f.kind}`}><Icon name={f.kind === 'sheet' ? 'sheet' : 'doc'} size={22} /></span><span className="lb">{f.title}</span>
            </button>
          ))}
        </div>

        {wins.map((w) => {
          const style = w.max ? { inset: 0, width: 'auto', height: 'auto', zIndex: w.z } : { left: w.x, top: w.y, width: w.w, height: w.h, zIndex: w.z }
          const on = focused?.k === w.k
          return (
            <div key={w.k} className={`ox-win ${on ? 'focus' : ''} ${w.anim || ''}`} style={{ ...style, display: w.min ? 'none' : 'flex' }} onPointerDown={() => focus(w.k)}>
              <div className="ox-title" onPointerDown={(e) => drag(e, w.k, 'move')} onDoubleClick={() => patch(w.k, { max: !w.max })}>
                {lights(w.k)}
                <span className="ttl"><AppTile k={w.k} size={16} />{titleOf(w)}</span>
              </div>
              <div className="ox-wbody">{body(w.k)}</div>
              {!w.max && <div className="ox-resize" onPointerDown={(e) => drag(e, w.k, 'resize')} />}
            </div>
          )
        })}
      </div>

      <div className="ox-notes" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.nid} className="ox-note"><AppTile k={t.app} size={34} /><div><b>{t.title}</b><p>{t.text}</p></div><small>now</small></div>
        ))}
      </div>

      <div className="ox-dock" onMouseMove={magnify} onMouseLeave={unmagnify} onPointerDown={(e) => e.stopPropagation()}>
        {APPS.map((x) => {
          const w = wins.find((y) => y.k === x.k)
          return (
            <button key={x.k} className="ox-dk" title={x.n} onClick={() => (w && !w.min && focused?.k === x.k ? minimise(x.k) : open(x.k, true))}>
              <span className={`ox-ico ${bounce === x.k ? 'bounce' : ''}`} ref={(el) => { dock.current[x.k] = el }}><AppTile k={x.k} size={46} /></span>
              {w && <i className="run" />}
            </button>
          )
        })}
      </div>

      <div className={`ox-cur ${cur.show ? 'show' : ''}`} style={{ transform: `translate(${cur.x}px, ${cur.y}px)`, '--dur': `${cur.dur}ms` }} aria-hidden>
        <svg width="20" height="24" viewBox="0 0 20 24"><path d="M2 1.5l15 11.2-6.6 1.1 3.9 7.2-2.9 1.6-3.9-7.2L2.5 20z" /></svg>
        <label style={{ background: a.color || '#7c4a8b' }}>{a.name}</label>
        {cur.click > 0 && <i className="ox-rip" key={cur.click} />}
      </div>

      {spot && <Spotlight id={id} onClose={() => setSpot(false)} onApp={(k) => open(k)} onDoc={showDoc} />}

      {asleep && (
        <div className="ox-lock" onClick={() => { setWoke(true); try { sessionStorage.setItem(`ox-woke-${id}`, '1') } catch { /* private mode */ } }}>
          <div className="t">{now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
          <div className="d">{now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' })}</div>
          <span className="av" style={{ background: a.color || '#7c4a8b' }}>{a.name.slice(0, 1)}</span>
          <b>{a.name}</b><small>Away from the desk. Click to wake the screen.</small>
        </div>
      )}

      {booting && (
        <div className="ox-boot"><span className="mark"><i /></span><div className="bar"><i /></div></div>
      )}
    </div>
  )
}

function safeJson(s) { try { return JSON.parse(s) } catch { return {} } }
