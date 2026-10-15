import { useEffect, useState } from 'react'

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** The apps on the desktop. `g` is the app-icon gradient. */
export const APPS = [
  { k: 'files', n: 'Files', i: 'folder', g: ['#5ac8fa', '#1479ff'], w: 900, h: 540 },
  { k: 'mail', n: 'Mail', i: 'mail', g: ['#4cb8ff', '#0a6cf0'], w: 980, h: 580 },
  { k: 'tasks', n: 'To-do', i: 'todo', g: ['#6ee08a', '#16a34a'], w: 920, h: 540 },
  { k: 'calendar', n: 'Calendar', i: 'calendar', g: ['#ffffff', '#f1f1f1'], w: 820, h: 560 },
  { k: 'text', n: 'Documents', i: 'doc', g: ['#ffd66b', '#f5a30a'], w: 700, h: 560 },
  { k: 'web', n: 'Browser', i: 'globe', g: ['#7dd3fc', '#2563eb'], w: 900, h: 580 },
  { k: 'phone', n: 'Calls', i: 'phone', g: ['#5eea7f', '#15a34a'], w: 600, h: 500 },
  { k: 'notes', n: 'Notes', i: 'note', g: ['#fff3a3', '#f7d21c'], w: 720, h: 500 },
  { k: 'projects', n: 'Projects', i: 'projects', g: ['#b794f6', '#6d3be0'], w: 660, h: 460 },
  { k: 'activity', n: 'Activity', i: 'activity', g: ['#555a63', '#1f2227'], w: 560, h: 520 },
]
export const appOf = (k) => APPS.find((a) => a.k === k)

/** Tools that are only looking around: nothing to show on screen. */
const QUIET = /^(list_|get_|check_|search_|team_|review_|project_status|world_time|date_math|connected_apps|read_document|read_email)/

/** Turn one history entry into something to show on screen, or null when it is not worth a scene. */
export function sceneOf(e) {
  const a = e.detail?.args || {}
  const d = e.detail || {}
  const t = e.tool
  if (!t || QUIET.test(t) || ['task', 'meeting', 'profile', 'manager', 'start_task', 'finish_task'].includes(t)) return null
  const who = (s) => String(s || '').trim()
  switch (t) {
    case 'create_document': return { app: 'text', kind: 'type', title: who(a.title) || 'Untitled', text: a.body || '', note: `Saved “${who(a.title) || 'Untitled'}”` }
    case 'update_document': return { app: 'text', kind: 'type', title: who(a.title) || `File #${a.id}`, text: a.body || '', note: 'Edited a file' }
    case 'create_spreadsheet': return { app: 'text', kind: 'sheet', title: who(a.title) || 'Untitled sheet', columns: safe(a.columns), rows: safe(a.rows), note: `Made “${who(a.title) || 'a spreadsheet'}”` }
    case 'edit_sheet': return { app: 'text', kind: 'sheet', title: `Spreadsheet #${a.id}`, columns: [], rows: safe(a.add_rows), note: 'Updated a spreadsheet' }
    case 'send_email': return { app: 'mail', kind: 'compose', to: who(a.to), subject: who(a.subject), text: a.body || '', note: `Sent an email to ${who(a.to)}` }
    case 'reply_email': return { app: 'mail', kind: 'compose', to: 'Reply', subject: 'Re:', text: a.body || '', note: 'Sent a reply' }
    case 'notify_admin': return { app: 'mail', kind: 'compose', to: 'admin', subject: who(a.subject), text: a.message || '', note: 'Messaged the admin' }
    case 'ask_colleague': return { app: 'mail', kind: 'compose', to: who(a.agent), subject: 'Quick question', text: a.question || '', note: `Asked ${who(a.agent)} a question` }
    case 'browser_open': case 'browser_state': case 'browser_click': case 'browser_type': case 'browser_select': case 'browser_scroll': case 'browser_back': case 'browser_login':
      return { app: 'web', kind: 'shot', url: d.url, title: d.title, shot: d.shot, at: d.at, text: d.snippet, action: t.replace('browser_', ''), note: `${t === 'browser_open' ? 'Opened' : t === 'browser_click' ? 'Clicked on' : t === 'browser_type' ? 'Typed on' : t === 'browser_login' ? 'Signed in on' : 'Browsing'} ${shortUrl(d.url)}` }
    case 'read_webpage': case 'learn_from_website': return { app: 'web', kind: 'browse', url: d.url || a.url, title: d.title, text: d.snippet, note: `Read ${shortUrl(d.url || a.url)}` }
    case 'web_search': case 'news_search': return { app: 'web', kind: 'search', query: who(a.query), results: d.results || [], note: `Searched for “${who(a.query)}”` }
    case 'add_todo': return { app: 'tasks', kind: 'todo', title: who(a.title), note: `Added a to-do: ${who(a.title)}` }
    case 'complete_duty': case 'update_todo': return { app: 'tasks', kind: 'todo-done', title: who(a.result || a.title) || 'To-do', note: 'Updated their to-do list' }
    case 'create_event': return { app: 'calendar', kind: 'event', title: who(a.title), start: a.start, note: `Added “${who(a.title)}” to the calendar` }
    case 'call_contact': case 'schedule_call': return { app: 'phone', kind: 'call', title: who(a.objective), note: 'Placed a call' }
    case 'remember': case 'learn_procedure': return { app: 'notes', kind: 'note', text: a.note || a.name, note: 'Made a note' }
    case 'copy_file': case 'create_folder': case 'move_file': case 'delete_file': return { app: 'files', kind: 'poke', note: e.summary }
    case 'create_project': case 'update_project': case 'post_update': case 'invite_to_project': return { app: 'projects', kind: 'poke', note: e.summary }
    default: return e.app && appOf(e.app) ? { app: e.app, kind: 'poke', note: e.summary } : { app: 'activity', kind: 'poke', note: e.summary }
  }
}

function safe(v) { try { const x = typeof v === 'string' ? JSON.parse(v) : v; return Array.isArray(x) ? x : [] } catch { return [] } }
export const shortUrl = (u) => String(u || '').replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0]

/** How long typing a text takes on screen: quick enough to watch, never longer than a few seconds. */
export const typeMs = (text, max = 4200) => Math.min(max, Math.max(500, String(text || '').length * 14))

/** How long writing an email takes on screen: To, Subject, the body, then pressing Send. */
export const composeMs = (s) => 600 + 800 + typeMs(s.text, 3600) + 1000

/** Reveal `text` one character at a time over `ms`, starting when `run` changes to a new truthy value. */
export function useTypewriter(text, ms, run) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!run) { setN(0); return undefined }
    const full = String(text || '')
    if (!full) { setN(0); return undefined }
    let raf, start
    const step = (t) => {
      start = start ?? t
      const k = Math.min(1, (t - start) / Math.max(1, ms))
      setN(Math.ceil(k * full.length))
      if (k < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [text, ms, run])
  return String(text || '').slice(0, n)
}
