import { useEffect, useState } from 'react'
import { api, usePoll } from './api'

export default function WorkspaceSwitcher() {
  const [data, reload] = usePoll('/workspaces', 15000)
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [visibility, setVisibility] = useState('private')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function act(fn) { setBusy(true); setError(''); try { await fn() } catch (e) { setError(e.message) } finally { setBusy(false) } }
  useEffect(() => { const sync = e => { if (e.key === 'workspace-changed') window.location.assign('/') }; window.addEventListener('storage', sync); return () => window.removeEventListener('storage', sync) }, [])
  async function enter(id) { await api.post(`/workspaces/${id}/switch`); localStorage.setItem('workspace-changed', String(Date.now())); window.location.assign('/') }
  const current = data?.workspaces.find(w => w.id === data.current)
  return <div className="workspace-switcher">
    <button className="workspace-trigger" onClick={() => setOpen(!open)} aria-expanded={open}><span><small>WORKSPACE</small><b>{current?.name || data?.company || 'Company'}</b></span><span>⌄</span></button>
    {open && <div className="workspace-panel">
      <h3>Your workspaces</h3><p className="muted small">Separate files, AI teammates, projects and connected apps.</p>
      <button className="workspace-choice" disabled={busy || !data} onClick={() => act(() => enter(data.company_id))}>{data?.company} <small>Company</small></button>
      {data?.workspaces.map(w => <button className="workspace-choice" disabled={busy} key={w.id} onClick={() => act(async () => { if (!w.accepted) await api.post(`/workspaces/${w.id}/accept`); await enter(w.id) })}><span>{w.name}</span><small>{!w.accepted ? 'Accept invitation →' : w.visibility === 'private' ? 'Private · only you' : 'Shared'}</small></button>)}
      <form className="form" onSubmit={e => { e.preventDefault(); act(async () => { const w = await api.post('/workspaces', { name, visibility }); await enter(w.id) }) }}>
        <h4>Create a workspace</h4><label>Name<input required maxLength={80} placeholder="My research, Product team…" value={name} onChange={e => setName(e.target.value)} /></label>
        <label>Who can access it?<select value={visibility} onChange={e => setVisibility(e.target.value)}><option value="private">Private — only me</option><option value="shared">Shared — invited colleagues</option></select></label>
        <button className="btn solid" disabled={busy || !name.trim()}>Create workspace</button>
      </form>
      {current?.is_owner && current?.visibility === 'shared' && <form className="form" onSubmit={e => { e.preventDefault(); act(async () => { await api.post(`/workspaces/${current.id}/invite`, { username: email }); setEmail(''); await reload(); setError('Invitation sent. Your colleague can accept it from their workspace menu.') }) }}><label>Invite a company colleague<input required placeholder="Email or username" value={email} onChange={e => setEmail(e.target.value)} /></label><button className="btn" disabled={busy}>Invite</button></form>}
      {current?.is_owner && current.visibility === 'shared' && <WorkspaceMembers id={current.id} ownerId={current.owner_id} />}
      {error && <p role="status" className="small">{error}</p>}
      <button className="link" onClick={() => setOpen(false)}>Close</button>
    </div>}
  </div>
}


function WorkspaceMembers({ id, ownerId }) {
  const [members, reload, error] = usePoll(`/workspaces/${id}/members`, 5000)
  const [failure, setFailure] = useState('')
  return <div><h4>Workspace members</h4>{members?.map(m => <div className="workspace-choice" key={m.id}><span>{m.display_name || m.username}<small> {m.accepted ? '' : 'Invited'}</small></span>{m.id !== ownerId && <button className="link" onClick={async () => { try { await api.del(`/workspaces/${id}/members/${m.id}`); await reload() } catch (e) { setFailure(e.message) } }}>Remove</button>}</div>)}{(failure || error) && <p role="alert">{failure || error.message}</p>}</div>
}
