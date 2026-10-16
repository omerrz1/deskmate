import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ago, usePoll } from '../api'
import { useAuth } from '../Auth'
import { useConfirm, useToast } from '../Feedback'
import Icon from '../Icon'
import PersonAvatar, { RoleBadge } from '../PersonAvatar'
import { PageHeader, Skeleton, useTitle } from '../ui'

const day = (ts) => new Date(ts * 1000).toLocaleDateString([], { day: 'numeric', month: 'long' })
const article = (w) => (/^[aeiou]/i.test(w) ? 'an' : 'a')
const STATUS = { active: '', temporary_password: 'Has not signed in yet' }

/** The humans in the company: owners, admins and members. (The AI employees are on the Team page.) */
export default function People() {
  useTitle('People')
  const auth = useAuth()
  const toast = useToast()
  const confirm = useConfirm()
  const [data, reload] = usePoll('/people', 0)
  const [projects] = usePoll('/projects', 0)
  const [inviting, setInviting] = useState(false)
  const [editing, setEditing] = useState(null)
  const [link, setLink] = useState(null)

  if (!data) return <div className="page"><Skeleton rows={5} /></div>
  const info = data.roles
  const used = data.people.length + data.invites.length

  const renew = async (inv) => {
    try { const r = await api.post(`/people/invites/${inv.id}/renew`); setLink({ ...r, name: inv.name }); reload() } catch (e) { toast.err(e.message) }
  }
  const cancel = async (inv) => {
    if (!(await confirm({ title: `Cancel the invitation for ${inv.email}?`, body: 'Their link will stop working.', confirm: 'Cancel invitation', danger: true }))) return
    try { await api.post(`/people/invites/${inv.id}/revoke`); reload() } catch (e) { toast.err(e.message) }
  }

  return (
    <div className="page pp-page">
      <PageHeader title="People" sub={`The humans in ${auth.workspace?.name || 'your company'}. Your AI employees are on the Team page.`}
        actions={<button className="btn solid big" onClick={() => setInviting(true)} disabled={used >= data.limit}><Icon name="userplus" size={15} /> Invite someone</button>} />

      <section className="pp-roles" aria-label="What each role can do">
        {['owner', 'admin', 'member'].map((r) => (
          <div key={r} className={`pp-role ${r}`}><RoleBadge role={r} /><p>{info[r].blurb}</p></div>
        ))}
      </section>

      <section className="pp-card">
        <header><h3>In the company <i>{data.people.length}</i></h3><span className="faint small">{used} of {data.limit} places used</span></header>
        {data.people.map((p) => (
          <div key={p.id} className="pp-row">
            <PersonAvatar name={p.name} size={40} />
            <div className="pp-who">
              <b>{p.name}{p.you && <span className="you">You</span>}</b>
              <small>{p.title ? `${p.title} · ` : ''}{p.email}</small>
            </div>
            <div className="pp-meta">
              <RoleBadge role={p.role} />
              {p.status === 'temporary_password' && <span className="pp-note">{STATUS[p.status]}</span>}
            </div>
            <div className="pp-proj">
              {p.role === 'member'
                ? (p.projects.length ? p.projects.map((x) => <Link key={x.id} to={`/projects/${x.id}`} className="chip small">{x.name}</Link>) : <span className="faint small">Not on any project yet</span>)
                : <span className="faint small">All projects</span>}
            </div>
            <div className="pp-seen faint small">{p.last_seen ? `Seen ${ago(p.last_seen)}` : 'Never'}</div>
            <div className="pp-act">
              {p.you ? <Link className="btn small" to="/settings/account">Your account</Link> : p.can_manage ? <button className="btn small" onClick={() => setEditing(p)}>Manage</button> : null}
            </div>
          </div>
        ))}
      </section>

      {data.invites.length > 0 && (
        <section className="pp-card">
          <header><h3>Invitations waiting <i>{data.invites.length}</i></h3><span className="faint small">They join by opening their link</span></header>
          {data.invites.map((inv) => (
            <div key={inv.id} className="pp-row invite">
              <span className="pav ghost" aria-hidden="true"><Icon name="mail" size={16} /></span>
              <div className="pp-who"><b>{inv.name || inv.email}</b><small>{inv.name ? `${inv.email} · ` : ''}invited by {inv.invited_by || 'an admin'}</small></div>
              <div className="pp-meta"><RoleBadge role={inv.role} /><span className="pp-note">Expires {day(inv.expires_at)}</span></div>
              <div className="pp-proj" />
              <div className="pp-seen" />
              <div className="pp-act">{inv.can_manage && <><button className="btn small" onClick={() => renew(inv)}>Get the link</button><button className="link" onClick={() => cancel(inv)}>Cancel</button></>}</div>
            </div>
          ))}
        </section>
      )}

      <p className="faint small pp-foot">Everyone signs in with their own email and password. Each person’s own files stay private to them; project and company files are shared. Anything an admin does is recorded with their name in the audit log under Settings.</p>

      {inviting && <InviteDialog info={info} projects={projects || []} company={auth.workspace?.name || 'your company'} onClose={() => setInviting(false)} onDone={reload} />}
      {link && <LinkDialog made={link} company={auth.workspace?.name || 'your company'} role={info[link.role]?.label || 'member'} onClose={() => setLink(null)} />}
      {editing && <EditDialog person={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); reload() }} />}
    </div>
  )
}

function CopyRow({ label, value, multiline }) {
  const [done, setDone] = useState(false)
  const copy = async () => { try { await navigator.clipboard.writeText(value); setDone(true); setTimeout(() => setDone(false), 1800) } catch { setDone(false) } }
  return (
    <div className="pp-copy">
      <div className="pp-copy-h"><span>{label}</span><button type="button" className="btn small" onClick={copy}><Icon name={done ? 'check' : 'copy'} size={13} /> {done ? 'Copied' : 'Copy'}</button></div>
      {multiline ? <pre>{value}</pre> : <input readOnly value={value} onFocus={(e) => e.target.select()} />}
    </div>
  )
}

/** What to send to the person: the link, and a message that is ready to paste into an email or chat. */
function Invitation({ made, company, role, name, from }) {
  const url = window.location.origin + made.join_path
  const first = (name || '').trim().split(/\s+/)[0]
  const message = `Hi${first ? ` ${first}` : ''},\n\n${from} has invited you to join ${company} on Deskmates as ${article(role)} ${role}.\n\nOpen this link to choose your password and join:\n${url}\n\nThe link works once and expires on ${day(made.expires_at)}.`
  return (
    <>
      <p className="muted">Send this to <b>{made.email}</b>. Nothing is emailed for you, so you choose how they get it.</p>
      <CopyRow label="Invitation link" value={url} />
      <CopyRow label="A message you can paste" value={message} multiline />
    </>
  )
}

function Dialog({ title, onClose, children, wide }) {
  return (
    <div className="modal-bg" onMouseDown={onClose}>
      <div className={`dialog pp-dialog ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  )
}

function InviteDialog({ info, projects, company, onClose, onDone }) {
  const auth = useAuth()
  const [f, setF] = useState({ email: '', name: '', role: 'member', project_ids: [] })
  const [made, setMade] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const live = projects.filter((p) => p.status !== 'done')
  const toggle = (id) => setF((x) => ({ ...x, project_ids: x.project_ids.includes(id) ? x.project_ids.filter((i) => i !== id) : [...x.project_ids, id] }))
  const submit = async (e) => {
    e.preventDefault(); setErr(''); setBusy(true)
    try { setMade(await api.post('/people/invite', { ...f, project_ids: f.role === 'member' ? f.project_ids : [] })); onDone() } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  if (made) {
    return (
      <Dialog title="Invitation ready" onClose={onClose} wide>
        <Invitation made={made} company={company} role={info[made.role].label} name={f.name} from={auth.name} />
        <div className="actions end"><button className="btn" onClick={() => { setMade(null); setF({ email: '', name: '', role: f.role, project_ids: [] }) }}>Invite someone else</button><button className="btn solid" onClick={onClose}>Done</button></div>
      </Dialog>
    )
  }
  return (
    <Dialog title="Invite someone" onClose={onClose} wide>
      <form className="form" onSubmit={submit}>
        <div className="row2">
          <label>Their email <span className="faint">(they sign in with it)</span><input autoFocus type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} placeholder="name@example.com" /></label>
          <label>Their name <span className="faint">(optional)</span><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="They can change it" /></label>
        </div>
        <fieldset className="pp-roleopts">
          <legend>What can they do?</legend>
          {['member', 'admin'].map((r) => (
            <label key={r} className={`pp-opt ${f.role === r ? 'on' : ''}`}>
              <input type="radio" name="role" checked={f.role === r} onChange={() => setF({ ...f, role: r })} />
              <span><RoleBadge role={r} /><small>{info[r].blurb}</small></span>
            </label>
          ))}
        </fieldset>
        {f.role === 'member' && (
          <fieldset className="pp-projects">
            <legend>Which projects can they work on? <span className="faint">(you can change this any time)</span></legend>
            {live.length === 0 ? <p className="faint small">No projects yet. Create one, then add them to it.</p>
              : live.map((p) => <label key={p.id} className="pp-check"><input type="checkbox" checked={f.project_ids.includes(p.id)} onChange={() => toggle(p.id)} />{p.name}</label>)}
          </fieldset>
        )}
        {f.role === 'admin' && <p className="pp-warn"><Icon name="shield" size={15} /> Admins can see and change everything except the owner's own settings, including customers, calls and files. Only invite people you trust with that.</p>}
        {err && <p className="warn" role="alert">{err}</p>}
        <div className="actions end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn solid" disabled={busy || !f.email.trim()}>{busy ? 'Creating…' : 'Create invitation'}</button></div>
      </form>
    </Dialog>
  )
}

function LinkDialog({ made, company, role, onClose }) {
  const auth = useAuth()
  return (
    <Dialog title="Invitation link" onClose={onClose} wide>
      <Invitation made={made} company={company} role={role} name={made.name} from={auth.name} />
      <div className="actions end"><button className="btn solid" onClick={onClose}>Done</button></div>
    </Dialog>
  )
}

function EditDialog({ person, onClose, onDone }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [f, setF] = useState({ name: person.name, title: person.title, role: person.role })
  const [busy, setBusy] = useState(false)
  const save = async (e) => {
    e.preventDefault(); setBusy(true)
    try {
      const body = { name: f.name, title: f.title }
      if (f.role !== person.role) body.role = f.role
      await api.patch(`/people/${person.id}`, body)
      toast.ok(f.role !== person.role ? `${f.name} is now ${article(f.role)} ${f.role}. They need to sign in again.` : 'Saved.')
      onDone()
    } catch (x) { toast.err(x.message) } finally { setBusy(false) }
  }
  const remove = async (keep) => {
    const ok = await confirm({
      title: `Remove ${person.name}?`, confirm: keep ? 'Remove and keep their files' : 'Remove and delete their files', danger: true,
      body: `They are signed out at once and lose access to the company. ${keep ? `Their files move to your files, in a folder called “From ${person.name}”.` : 'Their files are deleted for good.'}`,
    })
    if (!ok) return
    try { await api.del(`/people/${person.id}?keep_files=${keep}`); toast.ok(`${person.name} was removed.`); onDone() } catch (x) { toast.err(x.message) }
  }
  return (
    <Dialog title={`Manage ${person.name}`} onClose={onClose}>
      <form className="form" onSubmit={save}>
        <label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label>Job title <span className="faint">(optional)</span><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="e.g. Designer at an agency" /></label>
        <label>Role
          <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="member">Member: works on the projects they are added to</option><option value="admin">Admin: runs the workspace</option></select>
        </label>
        {f.role !== person.role && <p className="pp-warn"><Icon name="info" size={15} /> Changing the role signs them out. They sign in again to get the new access.</p>}
        <div className="actions end"><button type="button" className="btn" onClick={onClose}>Close</button><button className="btn solid" disabled={busy || !f.name.trim()}>Save</button></div>
      </form>
      <div className="pp-danger">
        <b>Remove from the company</b>
        <p className="muted small">They lose access straight away. You choose what happens to their own files.</p>
        <div className="actions"><button className="btn danger" onClick={() => remove(true)}>Remove, keep their files</button><button className="link" onClick={() => remove(false)}>Remove and delete their files</button></div>
      </div>
    </Dialog>
  )
}
