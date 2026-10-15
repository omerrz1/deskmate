import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, usePoll } from '../api'
import { AppIcon, EmptyState, PageHeader, Skeleton, Switch, useTitle } from '../ui'
import { useConfirm, useToast } from '../Feedback'
import Icon from '../Icon'

const SETUP = { key: 'API key', details: 'Account details' }
const TABS = [['connected', 'Connected'], ['add', 'Add an app'], ['builtin', 'Built in'], ['advanced', 'Advanced']]

/** Everything your team can use outside Deskmates: connected accounts, built-in tools, webhooks and MCP servers. */
export default function Apps() {
  useTitle('Apps')
  const toast = useToast()
  const confirm = useConfirm()
  const [params, setParams] = useSearchParams()
  const [cat] = usePoll('/apps/catalog', 0)
  const [inst, reload] = usePoll('/apps', 0)
  const [builtin, reloadB] = usePoll('/apps/builtin', 0)
  const [reqs] = usePoll('/app-requests', 0)
  const [sel, setSel] = useState(null) // { spec, inst? }
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('All')
  const want = params.get('connect')
  const tab = params.get('tab') || (want ? 'add' : inst && inst.length === 0 ? 'add' : 'connected')
  const setTab = (t) => setParams(t === 'connected' ? {} : { tab: t })
  const installed = inst || []
  const asked = (reqs || []).find((r) => r.app_key === want)

  useEffect(() => {  // arriving from an employee's request: open that app's form straight away
    if (!want || !cat || sel) return
    const spec = cat.find((a) => a.id === want)
    if (spec) setSel({ spec })
  }, [want, cat]) // eslint-disable-line react-hooks/exhaustive-deps

  const addable = useMemo(() => (cat || []).filter((a) => !a.builtin), [cat])
  const cats = useMemo(() => {  // categories with how many apps each holds, biggest first
    const n = {}
    addable.forEach((a) => { n[a.category] = (n[a.category] || 0) + 1 })
    return Object.entries(n).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))
  }, [addable])
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    const hit = (a) => words.every((w) => (a.name + ' ' + a.category + ' ' + a.description).toLowerCase().includes(w))
    return addable.filter((a) => (filter === 'All' || a.category === filter) && hit(a))
      .sort((x, y) => (q ? 0 : (y.popular ? 1 : 0) - (x.popular ? 1 : 0)) || x.name.localeCompare(y.name))
  }, [addable, filter, q])
  const appCard = (a) => (
    <article key={a.id} className="appcard">
      <div className="apphead"><AppIcon id={a.id} name={a.name} emoji={a.icon} /><div><b>{a.name}</b><small>{a.category}</small></div></div>
      <p className="small muted clamp2">{a.description}</p>
      <div className="appfoot">
        {a.installed && !a.multi ? <span className="tag done" style={{ margin: 0, width: 'fit-content' }}>Connected</span> : <button className="btn small" onClick={() => setSel({ spec: a })}>Connect</button>}
        {SETUP[a.setup] && <span className="faint small">{SETUP[a.setup]}</span>}
      </div>
    </article>
  )
  const featured = !q && filter === 'All' ? shown.filter((a) => a.popular) : []
  const toggle = async (i, patch) => { try { await api.put(`/apps/${i.id}`, patch); reload() } catch (e) { toast.err(e.message) } }
  const test = async (i) => { try { const r = await api.post(`/apps/${i.id}/test`); r.ok ? toast.ok(`${i.name}: ${r.message}`) : toast.err(`${i.name}: ${r.message}`); reload() } catch (e) { toast.err(e.message) } }
  const disconnect = async (i) => {
    if (!(await confirm({ title: `Disconnect ${i.name}?`, body: 'Your team will no longer be able to use it. You can connect it again any time.', confirm: 'Disconnect', danger: true }))) return
    try { await api.del(`/apps/${i.id}`); reload(); toast.ok(`${i.name} disconnected`) } catch (e) { toast.err(e.message) }
  }
  const closeModal = () => { setSel(null); if (want) setParams(tab === 'connected' ? {} : { tab }) }

  return (
    <div className="page wide">
      <PageHeader title="Apps" sub="The tools your team can use: your own accounts, built-in tools and custom connections." />
      <div className="tabs">{TABS.map(([k, n]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{n}{k === 'connected' && installed.length > 0 && <i className="badge muted-badge">{installed.length}</i>}</button>)}</div>

      {tab === 'connected' && (
        !inst ? <Skeleton rows={3} /> : installed.length === 0 ? (
          <EmptyState title="No apps connected yet" hint="Connect an account, such as Slack, Google Calendar or Stripe, and your team can use it." action={<button className="btn solid" onClick={() => setTab('add')}>Add an app</button>} />
        ) : (
          <div className="list">
            {installed.map((i) => {
              const spec = (cat || []).find((c) => c.id === i.kind)
              return (
                <div key={i.id} className="item app-row">
                  <AppIcon id={i.kind} name={i.name} emoji={i.icon} />
                  <div className="grow"><b>{i.name}</b><span className={i.last_error ? 'warn small' : 'muted small'} style={{ margin: 0, display: 'block' }}>{i.last_error || `${i.last_ok_at ? 'Connected' : 'Saved'} · ${i.tools.length} actions`}</span></div>
                  <div className="side-r app-ctl">
                    <Switch checked={i.enabled} label="On" onChange={(v) => toggle(i, { enabled: v })} />
                    <Switch checked={i.allow_write} label="Can make changes" onChange={(v) => toggle(i, { allow_write: v })} />
                    <button className="link" onClick={() => test(i)}>Test</button>
                    <button className="link" onClick={() => spec && setSel({ spec, inst: i })}>Settings</button>
                    <button className="link" onClick={() => disconnect(i)}>Disconnect</button>
                  </div>
                </div>
              )
            })}
          </div>
        )
      )}

      {tab === 'add' && (
        <>
          <div className="actions" style={{ marginBottom: 12 }}>
            <div className="search-box"><Icon name="search" size={15} /><input placeholder="Search apps" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          </div>
          <div className="chips">
            <button className={`chip ${filter === 'All' ? 'on' : ''}`} onClick={() => setFilter('All')}>All <i>{addable.length}</i></button>
            {cats.map(([c, n]) => <button key={c} className={`chip ${filter === c ? 'on' : ''}`} onClick={() => setFilter(c)}>{c} <i>{n}</i></button>)}
          </div>
          {!cat ? <Skeleton rows={4} /> : (
            <>
              {featured.length > 0 && (
                <>
                  <h3 className="grid-h">Popular</h3>
                  <div className="appgrid">{featured.map(appCard)}</div>
                  <h3 className="grid-h">All apps <span className="faint small">{shown.length - featured.length} more</span></h3>
                </>
              )}
              <div className="appgrid">{(featured.length ? shown.filter((a) => !a.popular) : shown).map(appCard)}</div>
              {shown.length === 0 && <p className="muted">No apps match. Employees can also use the Advanced tab to reach any service with a web address.</p>}
            </>
          )}
        </>
      )}

      {tab === 'builtin' && (
        <>
          <p className="muted" style={{ marginBottom: 14 }}>Ready to use, with no account or key. Switch one off if you don't want your team to have it.</p>
          {!builtin ? <Skeleton rows={4} /> : (
            <div className="list">
              {builtin.map((b) => (
                <div key={b.id} className="item">
                  <AppIcon id={b.id} name={b.name} emoji={(cat || []).find((c) => c.id === b.id)?.icon} />
                  <div className="grow"><b>{b.name}</b><span className="muted small" style={{ display: 'block' }}>{b.description}</span></div>
                  <div className="side-r"><Switch checked={b.enabled} label={b.enabled ? 'On' : 'Off'} onChange={async (v) => { try { await api.put(`/apps/builtin/${b.id}`, { enabled: v }); reloadB() } catch (e) { toast.err(e.message) } }} /></div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {tab === 'advanced' && <Advanced />}
      {sel && <AppModal sel={sel} asked={asked && sel.spec.id === asked.app_key ? asked : null} onClose={closeModal} onSaved={() => { reload() }} />}
    </div>
  )
}

function AppModal({ sel, asked, onClose, onSaved }) {
  const toast = useToast()
  const { spec, inst } = sel
  const [vals, setVals] = useState(inst ? { ...inst.config } : {})
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [msg, setMsg] = useState('')
  useEffect(() => { const k = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k) }, [onClose])
  const save = async () => {
    setBusy(true); setMsg(''); setRes(null)
    try {
      const r = inst ? await api.put(`/apps/${inst.id}`, { config: vals }) : await api.post('/apps', { kind: spec.id, config: vals })
      setRes(r.test); onSaved()
      if (!r.test || r.test.ok) { toast.ok(inst ? `${spec.name} updated` : `${spec.name} connected`); onClose() }
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  return (
    <div className="modal-bg" onMouseDown={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()} style={{ width: 'min(560px, 100%)' }}>
        <div className="modal-h"><div className="actions"><AppIcon id={spec.id} name={spec.name} emoji={spec.icon} size={30} /><h3>{inst ? `${spec.name} settings` : `Connect ${spec.name}`}</h3></div><button className="icon-x" onClick={onClose} aria-label="Close"><Icon name="x" size={16} /></button></div>
        <div className="modal-b form-grid">
          {asked && <div className="notice" style={{ cursor: 'default' }}><b>{asked.agent_name} asked for this.</b> {asked.why}</div>}
          <p className="muted">{spec.description}</p>
          {spec.help && <div className="tip">{spec.help}</div>}
          {spec.fields.length === 0 && <p className="small">This app needs no account or key.</p>}
          {spec.fields.map((f) => (
            <label key={f.key} className="f">{f.label}{!f.required && <span className="faint"> (optional)</span>}{f.secret && inst?.secrets_set?.[f.key] && <span className="ok small" style={{ margin: 0 }}> saved, leave empty to keep</span>}
              <input type={f.secret ? 'password' : 'text'} autoComplete="new-password" value={vals[f.key] ?? ''} placeholder={f.placeholder || (f.default ? `Default: ${f.default}` : '')} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
              {f.help && <small className="muted">{f.help}</small>}
            </label>
          ))}
          <details>
            <summary className="muted" style={{ cursor: 'pointer' }}>What your team will be able to do ({spec.tools.length})</summary>
            <ul className="plain" style={{ marginTop: 8 }}>{spec.tools.map((t) => <li key={t.name} className="small"><b>{t.name.replace(/_/g, ' ')}</b>{t.write && <span className="tag"> changes things</span>} <span className="muted">{t.description}</span></li>)}</ul>
          </details>
          {!spec.verified && <p className="muted small">Built from the service's public documentation. Use the test result to confirm it works with your account.</p>}
          {res && <p className={res.ok ? 'ok small' : 'warn small'}>{res.message}</p>}
          {msg && <p className="warn small">{msg}</p>}
          <div className="actions" style={{ justifyContent: 'flex-end' }}><button className="btn" onClick={onClose}>Cancel</button><button className="btn solid" disabled={busy} onClick={save}>{busy ? 'Connecting…' : inst ? 'Save and test' : 'Connect'}</button></div>
        </div>
      </div>
    </div>
  )
}

function Advanced() {
  return (
    <div style={{ display: 'grid', gap: 36 }}>
      <Messaging />
      <Hooks />
      <Mcp />
    </div>
  )
}

function Messaging() {
  const [c, reload] = usePoll('/central', 0)
  const toast = useToast()
  return (
    <section>
      <h3 className="sec-h">Texts and WhatsApp</h3>
      <p className="muted small" style={{ marginBottom: 10 }}>Whether a message to a customer goes out straight away or waits for your approval. Needs Twilio or WhatsApp Business connected.</p>
      <select style={{ maxWidth: 320 }} value={c?.messaging_policy || 'approve'} onChange={async (e) => { try { await api.put('/outbox/policy', { messaging_policy: e.target.value }); reload(); toast.ok('Saved') } catch (x) { toast.err(x.message) } }}>
        <option value="approve">Wait for my approval (recommended)</option><option value="auto">Send straight away</option>
      </select>
    </section>
  )
}

function Hooks() {
  const [list, reload] = usePoll('/hooks', 0)
  const [team] = usePoll('/agents', 0)
  const toast = useToast()
  const confirm = useConfirm()
  const [f, setF] = useState({ name: '', agent_id: '', instruction: '' })
  const [made, setMade] = useState(null)
  const create = async () => { try { const r = await api.post('/hooks', { ...f, agent_id: +f.agent_id }); setMade(r); setF({ name: '', agent_id: '', instruction: '' }); reload() } catch (e) { toast.err(e.message) } }
  return (
    <section>
      <h3 className="sec-h">Inbound webhooks</h3>
      <p className="muted small" style={{ marginBottom: 10 }}>Let another system hand an employee a task: Stripe, a web form, Zapier. The address is secret, so treat it like a password.</p>
      <div className="list">
        {(list || []).map((h) => (
          <div key={h.id} className="item"><div className="grow"><b>{h.name}</b><span className="muted small">{h.agent_name} · {h.uses} calls</span></div>
            <button className="link" onClick={async () => { if (await confirm({ title: `Delete ${h.name}?`, confirm: 'Delete', danger: true })) { await api.del(`/hooks/${h.id}`); reload() } }}>Delete</button></div>
        ))}
        {(list || []).length === 0 && <div className="empty-line">No webhooks yet.</div>}
      </div>
      <div className="form-grid" style={{ marginTop: 14, maxWidth: 560 }}>
        <div className="row2">
          <label className="f">Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="New website enquiry" /></label>
          <label className="f">Who handles it<select value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })}><option value="">Choose…</option>{(team || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        </div>
        <label className="f">What should they do with it?<input value={f.instruction} onChange={(e) => setF({ ...f, instruction: e.target.value })} placeholder="Add the person as a customer and email them a welcome" /></label>
        <div className="actions"><button className="btn" disabled={!f.name || !f.agent_id} onClick={create}>Create webhook</button></div>
        {made && <div className="result-box"><b>Copy this address now. It is shown only once.</b><code style={{ wordBreak: 'break-all' }}>{window.location.origin}{made.path}</code></div>}
      </div>
    </section>
  )
}

function Mcp() {
  const [list, reload] = usePoll('/connectors', 0)
  const toast = useToast()
  const confirm = useConfirm()
  const [f, setF] = useState({ name: '', url: '', token: '' })
  const [busy, setBusy] = useState(false)
  const add = async () => { setBusy(true); try { await api.post('/connectors', f); setF({ name: '', url: '', token: '' }); reload(); toast.ok('Server connected') } catch (e) { toast.err(e.message) } setBusy(false) }
  return (
    <section>
      <h3 className="sec-h">MCP servers</h3>
      <p className="muted small" style={{ marginBottom: 10 }}>For developers: connect any server that speaks MCP and your team can use its tools. Only servers on the public web are allowed.</p>
      <div className="list">
        {(list || []).map((c) => (
          <div key={c.id} className="item">
            <div className="grow"><b>{c.name}</b><span className={c.last_error ? 'warn small' : 'muted small'} style={{ margin: 0, display: 'block' }}>{c.last_error || `${c.tools.length} tool${c.tools.length === 1 ? '' : 's'} · ${c.url}`}</span></div>
            <div className="side-r">
              <Switch checked={c.enabled} label="On" onChange={async (v) => { await api.patch(`/connectors/${c.id}`, { enabled: v }); reload() }} />
              <button className="link" onClick={async () => { await api.post(`/connectors/${c.id}/refresh`); reload() }}>Check</button>
              <button className="link" onClick={async () => { if (await confirm({ title: `Remove ${c.name}?`, confirm: 'Remove', danger: true })) { await api.del(`/connectors/${c.id}`); reload() } }}>Remove</button>
            </div>
          </div>
        ))}
        {(list || []).length === 0 && <div className="empty-line">No servers connected.</div>}
      </div>
      <div className="form-grid" style={{ marginTop: 14, maxWidth: 560 }}>
        <div className="row2">
          <label className="f">Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Company CRM" /></label>
          <label className="f">Server address<input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://example.com/mcp" /></label>
        </div>
        <label className="f">Access token (if it needs one)<input type="password" autoComplete="new-password" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} /></label>
        <div className="actions"><button className="btn" disabled={!f.name || !f.url || busy} onClick={add}>{busy ? 'Connecting…' : 'Connect'}</button></div>
      </div>
    </section>
  )
}
