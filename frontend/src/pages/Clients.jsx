import { useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { api, usePoll, ago, dur, label } from '../api'
import { Panel, Badge, Empty, Ring, outcomeKind } from '../ui'
import { useConfirm, usePrompt } from '../Feedback'

function Client({ id, onChanged }) {
  const confirm = useConfirm()
  const prompt = usePrompt()
  const [c, reload] = usePoll(`/contacts/${id}`, 0, [id])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [msg, setMsg] = useState('')
  const [sch, setSch] = useState(null)
  const [camps] = usePoll('/campaigns', 0)
  if (!c) return <p className="muted">Loading…</p>
  const r = c.report

  const gen = async () => { setBusy(true); try { await api.post(`/contacts/${id}/report`); await reload() } catch (e) { setMsg(e.message) } setBusy(false) }
  const call = async () => {
    const objective = await prompt({ title: 'What should the agent do on this call?', value: 'Check in with the customer and see if they need anything.', confirm: 'Place call' })
    if (!objective) return
    await api.post('/calls/test', { contact_id: id, objective })
    setMsg('Call placed.')
  }
  const patch = async (p) => { await api.patch(`/contacts/${id}`, p); reload(); onChanged() }

  return (
    <div className="client">
      <div className="client-h">
        <div><h2 dir="auto">{c.name}</h2><small className="mono">{c.flag} {c.phone} · {c.country || 'unknown country'}{c.company ? ` · ${c.company}` : ''}</small>
          <div className="chips" style={{ marginTop: 8 }}><span className="small muted">Called in</span>
            <select style={{ width: 'auto' }} value={c.language} onChange={(e) => patch({ language: e.target.value })}>
              {[...new Set([c.language, 'English', 'Arabic', 'Hindi', 'Urdu', 'Bengali', 'Chinese', 'Indonesian', 'Tamil', 'Turkish', 'Persian', 'Spanish', 'French'])].map((l) => <option key={l}>{l}</option>)}
            </select>
            <span className="faint small">{c.lang_source === 'manual' ? 'set by hand' : c.lang_source === 'phone' ? 'from phone code' : c.lang_source === 'sheet' ? 'from spreadsheet' : ''}</span></div></div>
        <div className="actions">
          <button className="btn small solid" onClick={call} disabled={c.do_not_call}>Call now</button>
          <button className="btn small" onClick={() => setSch(sch ? null : { at: '', camp: '', objective: 'Follow up with the customer.' })} disabled={c.do_not_call}>Schedule call</button>
          <button className="btn small ghost" onClick={async () => {
            if (!(await confirm({ title: `Delete ${c.name}?`, body: 'This permanently deletes them and all their calls and transcripts. It cannot be undone.', confirm: 'Delete', danger: true }))) return
            await api.del(`/contacts/${id}`); onChanged(); window.history.back()
          }}>Delete all data</button>
          <button className="btn small" onClick={gen} disabled={busy}>{busy ? 'Analysing…' : r ? 'Refresh report' : 'Generate report'}</button>
        </div>
      </div>
      {sch && (
        <Panel title="Schedule a call">
          <div className="form">
            <div className="form-row">
              <label>When<input type="datetime-local" value={sch.at} onChange={(e) => setSch({ ...sch, at: e.target.value })} /></label>
              <label>Campaign<select value={sch.camp} onChange={(e) => setSch({ ...sch, camp: e.target.value })}><option value="">One-off call</option>{(camps || []).map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}</select></label>
            </div>
            {!sch.camp && <label>What should the agent do?<input value={sch.objective} onChange={(e) => setSch({ ...sch, objective: e.target.value })} /></label>}
            <div className="actions"><button className="btn solid" disabled={!sch.at} onClick={async () => {
              const r = await api.post('/calls/schedule', { contact_id: +id, at: new Date(sch.at).getTime() / 1000, campaign_id: sch.camp ? +sch.camp : null, objective: sch.objective })
              setMsg(r.adjusted ? 'Scheduled, moved to the next calling hours.' : 'Call scheduled.'); setSch(null); reload()
            }}>Schedule</button></div>
          </div>
        </Panel>
      )}
      {msg && <div className="notice" onClick={() => setMsg('')}>{msg}</div>}
      <div className="chips">
        <Badge kind={outcomeKind(c.status)}>{c.status}</Badge>
        {c.last_outcome && <Badge kind={outcomeKind(c.last_outcome)}>{c.last_outcome}</Badge>}
        {c.tags && <span className="chip static">{c.tags}</span>}
        <button className={`chip ${c.do_not_call ? 'on' : ''}`} onClick={() => patch({ do_not_call: !c.do_not_call })}>{c.do_not_call ? 'Do-not-call ON' : 'Mark do-not-call'}</button>
      </div>

      <Panel title="AI client report" right={r && <span className="muted small">{ago(r.generated_at)}</span>}>
        {r ? (
          <div className="report">
            <Ring value={r.score} />
            <div>
              <h4>{r.headline}</h4>
              <p>{r.profile}</p><p>{r.history}</p>
              <p className="muted small">Sentiment: {r.sentiment_trend}</p>
              {r.risks?.length > 0 && <ul className="points">{r.risks.map((x, i) => <li key={i}>{x}</li>)}</ul>}
              <p className="follow"><b>Next best action:</b> {r.next_best_action}</p>
            </div>
          </div>
        ) : <p className="muted">No report yet. Generate one from the full call history.</p>}
      </Panel>

      <div className="grid g-1-1">
        <Panel title="Customer data">
          <dl className="kv">
            {Object.entries({ email: c.email, ...c.extra }).filter(([, v]) => v).map(([k, v]) => <div key={k}><dt>{k}</dt><dd dir="auto">{v}</dd></div>)}
          </dl>
          <label className="notes">Notes the agent can use
            <textarea rows="3" dir="auto" value={note ?? c.notes ?? ''} onChange={(e) => setNote(e.target.value)} onBlur={() => note !== null && patch({ notes: note })} />
          </label>
        </Panel>
        <Panel title={`Call attempts · ${c.calls.length}`} pad={false}>
          {c.calls.length ? c.calls.map((k) => (
            <Link key={k.id} className="attempt" to={`/calls?open=${k.id}`}>
              <div><b>Try {k.attempt}</b> <Badge kind={outcomeKind(k.outcome || k.status)}>{k.outcome || k.status}</Badge><small>{ago(k.created_at)} · {dur(k.duration)}</small></div>
              <p>{k.summary || label(k.status)}</p>
            </Link>
          )) : <p className="muted pad">Never called.</p>}
        </Panel>
      </div>
    </div>
  )
}

export default function Clients() {
  const [sp, setSp] = useSearchParams()
  const [search, setSearch] = useState('')
  const [list, reload] = usePoll(`/contacts?limit=200&search=${encodeURIComponent(search)}`, 0, [search])
  const open = sp.get('open')
  return (
    <div className="page">
      <header className="page-h"><h1>Customer</h1></header>
      <div className="split clients">
        <Panel pad={false} className="client-list">
          <div className="filters"><input placeholder="Search clients…" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
          <div className="list-scroll">
            {(list?.items || []).map((c) => (
              <button key={c.id} className={`li ${String(c.id) === open ? 'sel' : ''}`} onClick={() => setSp({ open: c.id })}>
                <b dir="auto">{c.name}</b>
                <span>{c.last_outcome ? label(c.last_outcome) : 'not called'}{c.do_not_call ? ' · DNC' : ''}</span>
              </button>
            ))}
          </div>
        </Panel>
        <div>{open ? <Client key={open} id={open} onChanged={reload} /> : <Empty title="Select a client" hint="See their call attempts, an AI-written report and edit notes the agent will use on the next call." />}</div>
      </div>
    </div>
  )
}
