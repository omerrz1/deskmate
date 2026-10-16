/** One shared connection to /api/stream. The server says which tables changed; screens showing them refresh at once. */
let es = null
let ok = false
const listeners = new Set()

function connect() {
  if (es || typeof EventSource === 'undefined') return
  es = new EventSource('/api/stream')
  es.onopen = () => { ok = true }
  es.onerror = () => { ok = false }  // the browser reconnects by itself
  es.onmessage = (e) => {
    ok = true
    try { const { t } = JSON.parse(e.data); if (t) listeners.forEach((f) => f(t)) } catch { /* a heartbeat or a half message */ }
  }
}

/** Call fn(tables) whenever something changes. Returns the function that stops listening. */
export function onChange(fn) {
  connect()
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
    if (!listeners.size && es) { es.close(); es = null; ok = false }
  }
}

export const liveOk = () => ok

/** Which tables each screen's data comes from. Addresses not listed here keep polling as before. */
const MAP = [
  [/^\/agents\/\d+\/activity/, ['agent_activity']],
  [/^\/agents\/\d+\/status/, ['duties', 'agent_activity', 'meetings']],
  [/^\/agents\/\d+\/calls/, ['calls']],
  [/^\/agents\/\d+\/recent-files/, ['documents']],
  [/^\/agents/, ['agents', 'duties', 'meetings', 'agent_notes']],
  [/^\/attention/, ['emails', 'meeting_requests', 'outbox', 'app_requests', 'discuss_items', 'integrations', 'calls', 'chat_messages', 'chats']],
  [/^\/chats\/calls/, ['calls']],
  [/^\/chats/, ['chats', 'chat_messages']],
  [/^\/projects/, ['projects', 'duties', 'project_members', 'project_updates', 'documents', 'agents']],
  [/^\/mail/, ['emails']],
  [/^\/outbox/, ['outbox']],
  [/^\/tasks/, ['duties']],
  [/^\/events/, ['events']],
  [/^\/docs/, ['documents']],
  [/^\/fs/, ['documents', 'folders']],
  [/^\/central/, ['settings', 'duties']],
  [/^\/pulse/, ['duties', 'agents', 'agent_activity']],
  [/^\/apps/, ['integrations', 'settings']],
  [/^\/discuss/, ['discuss_items']],
  [/^\/requests/, ['meeting_requests']],
  [/^\/campaigns/, ['campaigns', 'calls']],
  [/^\/calls/, ['calls']],
  [/^\/activity/, ['activity']],
  [/^\/skills/, ['skills']],
  [/^\/live-questions/, ['live_questions']],
]
export const topicsFor = (url) => (MAP.find(([re]) => re.test(url)) || [null, []])[1]
