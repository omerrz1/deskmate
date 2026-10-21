import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, usePoll } from '../api'
import { Panel, Badge, Empty, outcomeKind } from '../ui'

const FIELDS = ['name', 'phone', 'language']

export default function Data() {
  const nav = useNavigate()
  const [search, setSearch] = useState('')
  const [language, setLanguage] = useState('')
  const qs = new URLSearchParams(Object.entries({ search, language }).filter(([, v]) => v)).toString()
  const [list, reload] = usePoll(`/contacts?limit=200&${qs}`, 0, [qs])
  const [segs] = usePoll('/segments', 0, [list?.total])
  const [prev, setPrev] = useState(null)
  const [file, setFile] = useState(null)
  const [mapping, setMapping] = useState({})
  const [msg, setMsg] = useState('')
  const input = useRef()

  const doImport = async (f, m) => {
    const fd = new FormData(); fd.append('file', f); fd.append('mapping', JSON.stringify(m)); fd.append('name', f.name)
    const r = await api.form('/datasets/import', fd)
    setMsg(`Added ${r.imported} customers.`); setPrev(null); reload()
  }
  const pick = async (f) => {
    if (!f) return
    setMsg('Reading file…')
    try {
      const fd = new FormData(); fd.append('file', f)
      const p = await api.form('/datasets/preview', fd)
      if (p.mapping.phone && p.mapping.name) await doImport(f, p.mapping)
      else { setFile(f); setPrev(p); setMapping(p.mapping); setMsg('') }
    } catch (e) { setMsg(e.message) }
  }

  return (
    <div className="page">
      <header className="page-h">
        <h1>Customers</h1>
        <div className="actions">
          <button className="btn solid" onClick={() => input.current.click()}>Upload Excel</button>
          <input ref={input} type="file" hidden accept=".xlsx,.xls,.csv" onChange={(e) => pick(e.target.files[0])} />
        </div>
      </header>
      {msg && <div className="notice" onClick={() => setMsg('')}>{msg}</div>}

      {prev && (
        <Panel title="Which columns are which?">
          <div className="map">
            {FIELDS.map((f) => (
              <label key={f}><span>{f}</span>
                <select value={mapping[f] || ''} onChange={(e) => setMapping((m) => ({ ...m, [f]: e.target.value }))}>
                  <option value="">—</option>{prev.columns.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
            ))}
          </div>
          <div className="actions">
            <button className="btn solid" disabled={!mapping.phone} onClick={() => doImport(file, mapping)}>Import {prev.rows} rows</button>
            <button className="btn ghost" onClick={() => setPrev(null)}>Cancel</button>
          </div>
        </Panel>
      )}

      {list && list.total === 0 && !search && !language ? (
        <Empty title="No customers yet" hint="Upload your Excel sheet. Names, phones and languages are detected automatically." />
      ) : (
        <Panel pad={false}>
          <div className="filters">
            <input placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="">All languages</option>
              {(segs?.language || []).map((o) => <option key={o.v} value={o.v}>{o.v} ({o.n})</option>)}
            </select>
            <button className="btn small" disabled={!list?.total} onClick={() => nav('/campaigns?new=1', { state: { filters: Object.fromEntries(Object.entries({ search, language }).filter(([, v]) => v)) } })}>Call these {list?.total || ''}</button>
          </div>
          <table className="tbl">
            <tbody>
              {(list?.items || []).map((c) => (
                <tr key={c.id} onClick={() => nav(`/clients?open=${c.id}`)}>
                  <td className="strong" dir="auto">{c.name}</td>
                  <td className="mono">{c.phone}</td>
                  <td>{c.language}</td>
                  <td>{c.last_outcome ? <Badge kind={outcomeKind(c.last_outcome)}>{c.last_outcome}</Badge> : <span className="muted">not called</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  )
}
