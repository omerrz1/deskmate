import { api, usePoll } from '../../api'
import { useConfirm, useToast } from '../../Feedback'

/** Messages sent from the public Contact page. */
function Messages() {
  const toast = useToast()
  const confirm = useConfirm()
  const [msgs, reload] = usePoll('/platform/messages', 15000)
  if (!msgs) return null
  const fresh = msgs.filter((m) => m.status === 'new').length
  const mark = async (m, status) => { try { await api.post(`/platform/messages/${m.id}/status`, { status }); reload() } catch (e) { toast.err(e.message) } }
  const drop = async (m) => {
    if (!(await confirm({ title: 'Delete this message?', body: 'It is removed for good.', confirm: 'Delete', danger: true }))) return
    try { await api.del(`/platform/messages/${m.id}`); reload() } catch (e) { toast.err(e.message) }
  }
  const when = (t) => new Date(t * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  return (
    <section className="panel" style={{ marginBottom: 16 }}><div className="panel-b">
      <h3>Messages from the Contact page ({fresh} new)</h3>
      <p className="muted small" style={{ marginBottom: 10 }}>Anyone can write to you from the public site. Reply from your own email; the button opens a draft.</p>
      {msgs.length === 0 && <p className="muted small">No messages yet.</p>}
      {msgs.map((m) => (
        <div key={m.id} style={{ borderTop: '1px solid var(--line)', padding: '12px 0', opacity: m.status === 'done' ? 0.6 : 1 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <b>{m.name || m.email}</b>
            <span className="faint small">{m.name ? m.email : ''}</span>
            <span className="faint small" style={{ marginLeft: 'auto' }}>{m.topic_label} · {when(m.created_at)}</span>
          </div>
          <p style={{ whiteSpace: 'pre-wrap', margin: '6px 0 8px' }}>{m.message}</p>
          <div className="actions">
            <a className="btn small" href={`mailto:${m.email}?subject=${encodeURIComponent('Re: your message to Deskmates')}`}>Reply</a>
            {m.status === 'new' ? <button className="btn small" onClick={() => mark(m, 'done')}>Mark as done</button> : <button className="btn small" onClick={() => mark(m, 'new')}>Mark as new</button>}
            <button className="link danger" onClick={() => drop(m)}>Delete</button>
          </div>
        </div>
      ))}
    </div></section>
  )
}

/** For whoever runs this server: every company workspace, when it was made and last used, and a switch to suspend one. */
export default function Platform() {
  const toast = useToast()
  const confirm = useConfirm()
  const [rows, reload] = usePoll('/platform/workspaces', 15000)
  if (!rows) return null
  const set = async (w, status) => {
    if (status === 'suspended' && !(await confirm({ title: `Suspend ${w.name}?`, body: 'Nobody in that company can sign in until you switch it back on. Their data is kept.', confirm: 'Suspend', danger: true }))) return
    try { await api.post(`/platform/workspaces/${w.id}/status`, { status }); reload() } catch (e) { toast.err(e.message) }
  }
  const when = (t) => (t ? new Date(t * 1000).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) : '–')
  return (
    <>
    <Messages />
    <section className="panel"><div className="panel-b">
      <h3>Workspaces on this server ({rows.length})</h3>
      <p className="muted small" style={{ marginBottom: 10 }}>Each company is kept completely apart: its own database, files, keys and apps. You see only these details, never their data.</p>
      <table className="tbl"><thead><tr><td>Company</td><td>Owner</td><td className="num">Admins</td><td className="num">Created</td><td className="num">Last sign-in</td><td /></tr></thead><tbody>
        {rows.map((w) => (
          <tr key={w.id}><td><b>{w.name}</b><small>{w.id === 'main' ? 'main workspace' : w.id}</small></td><td>{w.owner || '–'}</td><td className="num">{w.members}</td><td className="num">{when(w.created_at)}</td><td className="num">{when(w.last_seen)}</td>
            <td className="num">{w.id === 'main' ? <span className="faint small">this one</span> : w.status === 'deleted' ? <span className="faint small">deleted</span> : w.status === 'active' ? <button className="link danger" onClick={() => set(w, 'suspended')}>Suspend</button> : <button className="link" onClick={() => set(w, 'active')}>Switch on</button>}</td></tr>
        ))}
      </tbody></table>
    </div></section>
    </>
  )
}
