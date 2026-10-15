import { useEffect, useState } from 'react'
import { api } from '../../api'

const FILTERS = [['all', 'Everything'], ['changes', 'Changes'], ['signins', 'Sign-ins'], ['failed', 'Problems']]

/** Who did what: sign-ins, key and setting changes, approvals and deletions. */
export default function Audit() {
  const [kind, setKind] = useState('all')
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  const [rows, setRows] = useState(null)
  const [more, setMore] = useState(false)
  useEffect(() => { const t = setTimeout(() => setDq(q.trim()), 250); return () => clearTimeout(t) }, [q])
  const qs = `kind=${kind}&q=${encodeURIComponent(dq)}`
  useEffect(() => { let on = true; api.get(`/audit?${qs}&limit=100`).then((r) => { if (on) { setRows(r); setMore(r.length === 100) } }).catch(() => on && setRows([])); return () => { on = false } }, [qs])
  const load = async () => { const r = await api.get(`/audit?${qs}&limit=100&before=${rows[rows.length - 1].id}`); setRows([...rows, ...r]); setMore(r.length === 100) }
  return (
    <section className="panel"><div className="panel-b">
      <div className="pj-bar-row" style={{ margin: '0 0 12px' }}>
        <div className="chips" style={{ margin: 0 }}>{FILTERS.map(([k, n]) => <button key={k} className={`chip ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>{n}</button>)}</div>
        <div className="actions"><div className="search-box"><input placeholder="Search" aria-label="Search the audit log" value={q} onChange={(e) => setQ(e.target.value)} /></div><a className="btn" href={`/api/audit.csv?${qs}`}>Export CSV</a></div>
      </div>
      {!rows ? <p className="faint">Loading…</p> : rows.length === 0 ? <p className="faint">Nothing matches.</p> : (
        <table className="tbl"><tbody>{rows.map((r) => (
          <tr key={r.id}><td className="num" style={{ whiteSpace: 'nowrap' }}>{new Date(r.ts * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</td>
            <td>{r.message}{r.meta?.ip && <small className="mono">{r.meta.ip}</small>}</td></tr>
        ))}</tbody></table>
      )}
      {more && <button className="btn" style={{ marginTop: 12 }} onClick={load}>Load more</button>}
    </div></section>
  )
}
