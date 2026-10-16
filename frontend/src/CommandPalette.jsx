import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from './api'
import Icon from './Icon'
import { AppIcon } from './ui'
import { useToast } from './Feedback'

const PAGES = [
  ['Team', '/', 'users'], ['Performance (scorecards)', '/performance', 'activity'], ['Templates (teams, projects, playbooks)', '/templates', 'copy'], ['Projects', '/projects', 'projects'], ['People: invite and manage the people in your company', '/people', 'person'], ['Files: yours, the company drive, projects and the AI team’s computers', '/files', 'folder'], ['Inbox', '/inbox', 'inbox'], ['Chats (WhatsApp, Instagram, Messenger)', '/chats', 'chat'], ['Chat and WhatsApp call settings', '/settings/chats', 'sliders'], ['Apps', '/apps', 'apps'], ['Settings', '/settings', 'sliders'],
  ['Settings: Company', '/settings/company', 'sliders'], ['Settings: Calling', '/settings/calling', 'phone'], ['Settings: Email', '/settings/email', 'mail'],
  ['Settings: Limits and AI costs', '/settings/costs', 'sliders'], ['Settings: Files', '/settings/files', 'folder'], ['Settings: Your account and security', '/settings/account', 'lock'],
]

/** Score how well a query matches a label: word starts beat plain substrings; 0 means no match. */
function score(label, q) {
  if (!q) return 1
  const l = label.toLowerCase()
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  let total = 0
  for (const w of words) {
    const i = l.indexOf(w)
    if (i < 0) return 0
    total += i === 0 ? 6 : l[i - 1] === ' ' || l[i - 1] === ':' ? 4 : 2
  }
  return total
}

/** ⌘K / Ctrl+K: jump to any person, project, app or setting, or run a quick action, from the keyboard. */
export default function CommandPalette({ open, onClose, paused, onTogglePause }) {
  const nav = useNavigate()
  const toast = useToast()
  const [q, setQ] = useState('')
  const [at, setAt] = useState(0)
  const [data, setData] = useState({ agents: [], projects: [], apps: [] })
  const [hits, setHits] = useState([])  // matches inside tasks, emails, files, calendar and customers
  const input = useRef(null)
  const list = useRef(null)

  useEffect(() => {
    if (!open) return
    setQ(''); setAt(0)
    setTimeout(() => input.current?.focus(), 0)
    let on = true
    Promise.all([api.get('/agents').catch(() => []), api.get('/projects').catch(() => []), api.get('/apps/catalog').catch(() => [])])
      .then(([agents, projects, apps]) => on && setData({ agents: agents || [], projects: projects || [], apps: (apps || []).filter((a) => !a.builtin) }))
    return () => { on = false }
  }, [open])

  useEffect(() => {
    const term = q.trim()
    if (!open || term.length < 2) { setHits([]); return undefined }
    let on = true
    const t = setTimeout(() => api.get(`/search?q=${encodeURIComponent(term)}`).then((r) => on && setHits(r)).catch(() => on && setHits([])), 220)
    return () => { on = false; clearTimeout(t) }
  }, [q, open])

  const go = useCallback((to) => { onClose(); nav(to) }, [nav, onClose])
  const meet = useCallback(async (a) => {
    onClose()
    try { const m = await api.post('/meetings', { agent_ids: [a.id] }); nav(`/meeting/${m.id}`) } catch (e) { toast.err(e.message) }
  }, [nav, onClose, toast])

  const items = useMemo(() => {
    const all = [
      { g: 'Actions', label: 'New agent', hint: 'Hire someone', icon: 'plus', run: () => go('/?new=1') },
      { g: 'Actions', label: 'Team meeting', hint: 'Start a meeting', icon: 'users', run: () => go('/?meet=1') },
      { g: 'Actions', label: paused ? 'Resume the team' : 'Pause all work', hint: paused ? 'Let everyone work again' : 'Stop work everywhere', icon: paused ? 'play' : 'pause', run: () => { onClose(); onTogglePause() } },
      ...PAGES.map(([label, to, icon]) => ({ g: 'Go to', label, icon, run: () => go(to) })),
      ...data.agents.flatMap((a) => [
        { g: 'People', label: a.name, hint: a.role || 'Team member', agent: a, run: () => go(`/agents/${a.id}`) },
        { g: 'People', label: `Meet ${a.name}`, hint: 'One-to-one meeting', agent: a, run: () => meet(a) },
      ]),
      ...data.projects.map((p) => ({ g: 'Projects', label: p.name, hint: p.status === 'done' ? 'Done' : 'Active', icon: 'projects', run: () => go(`/projects/${p.id}`) })),
      ...data.apps.map((a) => ({ g: 'Apps', label: a.installed && !a.multi ? a.name : `Connect ${a.name}`, hint: a.category, app: a, run: () => go(a.installed && !a.multi ? '/apps' : `/apps?connect=${a.id}`) })),
    ]
    if (!q.trim()) return all.filter((i) => i.g === 'Actions' || i.g === 'Go to').slice(0, 9)
    const found = all.map((i) => ({ i, s: score(`${i.label} ${i.hint || ''}`, q.trim()) + (score(i.label, q.trim()) ? 3 : 0) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s).slice(0, 9).map((x) => x.i)
    const ICON = { Tasks: 'todo', Emails: 'mail', Files: 'doc', Calendar: 'calendar', Customers: 'users' }
    const inside = hits.flatMap((g) => g.items.map((it) => ({ g: g.kind, label: it.title, hint: it.sub, icon: ICON[g.kind] || 'search', run: () => go(it.to) })))
    return [...found, ...inside.slice(0, 14)]
  }, [q, data, hits, paused, go, meet, onClose, onTogglePause])

  useEffect(() => { setAt(0) }, [q])
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [at, items])

  if (!open) return null
  const key = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setAt((x) => Math.min(x + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setAt((x) => Math.max(x - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); items[at]?.run() }
  }
  let last = ''
  return (
    <div className="cmdk-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Search and commands" onKeyDown={key}>
        <div className="cmdk-in">
          <Icon name="search" size={16} />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search tasks, emails, files, people, projects, apps… or type a command" aria-label="Search" role="combobox" aria-expanded="true" aria-controls="cmdk-list" />
          <kbd>esc</kbd>
        </div>
        <ul className="cmdk-list" id="cmdk-list" role="listbox" ref={list}>
          {items.length === 0 && <li className="cmdk-empty">Nothing matches "{q}".</li>}
          {items.map((it, n) => {
            const head = it.g !== last ? <li className="cmdk-g" role="presentation">{it.g}</li> : null
            last = it.g
            return (
              <Fragment key={`${it.g}-${it.label}-${n}`}>
                {head}
                <li role="option" aria-selected={n === at} className={n === at ? 'on' : ''} onMouseMove={() => at !== n && setAt(n)} onClick={() => it.run()}>
                  {it.app ? <AppIcon id={it.app.id} name={it.app.name} emoji={it.app.icon} size={24} />
                    : it.agent ? <span className="cmdk-av" style={{ background: it.agent.color || '#221d1b' }}>{it.agent.name.slice(0, 1).toUpperCase()}</span>
                      : <span className="cmdk-ic"><Icon name={it.icon || 'bolt'} size={15} /></span>}
                  <span className="cmdk-l">{it.label}</span>
                  {it.hint && <span className="cmdk-h" title={it.hint}>{it.hint}</span>}
                  {n === at && <kbd>↵</kbd>}
                </li>
              </Fragment>
            )
          })}
        </ul>
        <div className="cmdk-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span></div>
      </div>
    </div>
  )
}
