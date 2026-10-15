/** Round profile picture for an AI team member: their colour and initial, with a soft ring while they speak. */
export default function Avatar({ agent, size = 64, speaking = false, thinking = false }) {
  const c = agent?.color || '#221d1b'
  return (
    <span className={`avatar ${speaking ? 'speaking' : ''} ${thinking ? 'thinking' : ''}`} style={{ width: size, height: size, fontSize: size * 0.42, '--c': c }}>
      <b>{(agent?.name || '?').slice(0, 1).toUpperCase()}</b>
    </span>
  )
}
