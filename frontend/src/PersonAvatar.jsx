const PALETTE = ['#0f8b8d', '#7a4fd6', '#2f6fed', '#c2410c', '#2a9d62', '#b8398e', '#8a6d1d', '#4b5fd0']
const hash = (s) => Array.from(String(s || '')).reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7)

/** A person who signs in (an owner, admin or member). Round, with their initials on a soft tint, so they are never mistaken for an AI employee, whose avatar is a solid rounded square. */
export default function PersonAvatar({ name, size = 36, title }) {
  const c = PALETTE[hash(name) % PALETTE.length]
  const initials = String(name || '?').trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?'
  return <span className="pav" style={{ width: size, height: size, fontSize: size * 0.4, '--c': c }} title={title || name} aria-hidden="true">{initials}</span>
}

/** The word that goes with a role: colours are never the only signal. */
export function RoleBadge({ role }) {
  const label = { owner: 'Owner', admin: 'Admin', member: 'Member' }[role] || role
  return <span className={`role-badge ${role}`}>{label}</span>
}

/** A small "AI" tag for the things that are AI employees, so people and AI employees are told apart at a glance. */
export function AiTag() {
  return <span className="ai-tag" title="An AI employee">AI</span>
}
