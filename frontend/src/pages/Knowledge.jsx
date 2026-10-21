import { Fragment, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, usePoll } from '../api'
import { Panel, Badge, Empty } from '../ui'

const FIELDS = [
  ['agent_name', 'Agent name (optional)', 'Leave empty to use the caller\'s own name', 1],
  ['persona', 'Persona: who the agent is and how it sounds', 'Friendly, quick, plain-spoken. Never pushy.', 3],
  ['context', 'Context: background about the organisation and why you are calling', 'What the organisation does, who the people are, what they asked for…', 4],
  ['key_facts', 'Key facts the agent must always know', 'Address, opening hours, deadlines, headline prices, what is NOT offered…', 4],
  ['next_steps', 'Normal next steps', 'e.g. Request a quote, then a site visit, then a booking', 2],
  ['contact', 'Hand-off contact', 'e.g. Sales: 020 0000 0000, hello@example.com', 1],
]

function Editor({ id, onChanged }) {
  const [kb, reload] = usePoll(`/kb/${id}`, 0, [id])
  const [f, setF] = useState(null)
  const [saved, setSaved] = useState('')
  const [doc, setDoc] = useState({ title: '', text: '' })
  const [q, setQ] = useState('')
  const [hits, setHits] = useState(null)
  const [open, setOpen] = useState(null)
  const [pron, setPron] = useState(null)
  const [pronMsg, setPronMsg] = useState('')
  useEffect(() => { api.get(`/kb/${id}/pronunciations`).then(setPron).catch(() => setPron([])) }, [id])
  const [cat, setCat] = useState('')
  const [busy, setBusy] = useState(false)
  const file = useRef()
  useEffect(() => { if (kb) setF({ name: kb.name, agent_name: kb.agent_name, persona: kb.persona, context: kb.context, key_facts: kb.key_facts, next_steps: kb.next_steps, contact: kb.contact }) }, [kb?.id]) // eslint-disable-line
  if (!kb || !f) return <p className="muted">Loading…</p>

  const save = async () => { await api.put(`/kb/${id}`, f); setSaved('Saved'); setTimeout(() => setSaved(''), 1800); onChanged(); reload() }
  const addDoc = async () => { await api.post(`/kb/${id}/docs`, { ...doc, kind: 'info' }); setDoc({ title: '', text: '' }); reload(); onChanged() }
  const upload = async (files) => {
    if (!files?.length) return
    const fd = new FormData(); [...files].forEach((x) => fd.append('files', x))
    setBusy(true); setSaved('Reading and sorting…')
    try { const r = await api.form(`/kb/${id}/upload`, fd); setSaved(`Added ${r.files.reduce((n, x) => n + x.sections, 0)} sections from ${r.files.length} file(s)`); reload(); onChanged() } catch (e) { setSaved(e.message) }
    setBusy(false)
  }
  const resort = async () => { setBusy(true); setSaved('Sorting…'); await api.post(`/kb/${id}/resort`); setBusy(false); setSaved('Sorted'); reload() }
  const setCategory = async (d, category) => { await api.patch(`/kb/${id}/docs/${d.id}`, { category }); reload() }
  const test = async () => { const r = await api.post('/knowledge/search', { kb_id: id, query: q }); setHits(r.results) }
  const show = async (d) => setOpen(open?.id === d.id ? null : await api.get(`/kb/${id}/docs/${d.id}`))

  return (
    <div>
      <Panel title="Persona & context">
        <div className="form">
          <label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          {FIELDS.map(([k, label, ph, rows]) => (
            <label key={k}>{label}
              {rows === 1 ? <input value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} placeholder={ph} />
                : <textarea rows={rows} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} placeholder={ph} />}
            </label>
          ))}
          <div className="actions"><button className="btn solid" onClick={save}>Save</button>{saved && <span className="ok small">{saved}</span>}</div>
        </div>
      </Panel>

      <Panel title={`Documents · ${kb.docs.length}`} right={<span className="actions"><button className="btn small ghost" disabled={busy} onClick={resort}>Sort with AI</button><button className="btn small" disabled={busy} onClick={() => file.current.click()}>Upload files</button></span>} pad={false}>
        <input ref={file} type="file" multiple hidden accept=".txt,.md,.csv,.pdf,.docx,.xlsx" onChange={(e) => { upload(e.target.files); e.target.value = '' }} />
        <div className="pad">
          <p className="muted small">Things the agent can look up mid-call: FAQs, products or services, prices, requirements, policies. Upload PDFs, Excel, text or markdown files: long files are split into topics and sorted into categories automatically. Or write a topic below.</p>
          <div className="form" style={{ marginTop: 12 }}>
            <input value={doc.title} onChange={(e) => setDoc({ ...doc, title: e.target.value })} placeholder="Topic, e.g. Refund policy" />
            <textarea rows="3" value={doc.text} onChange={(e) => setDoc({ ...doc, text: e.target.value })} placeholder="Details the agent should know" />
            <div className="actions"><button className="btn small solid" disabled={!doc.title || !doc.text} onClick={addDoc}>Add topic</button></div>
          </div>
        </div>
        {(() => {
          const counts = {}
          kb.docs.forEach((d) => { counts[d.category] = (counts[d.category] || 0) + 1 })
          const cats = Object.keys(counts)
          const shown = cat ? kb.docs.filter((d) => d.category === cat) : kb.docs
          return (
            <>
              <div className="chips pad" style={{ margin: 0 }}>
                <button className={`chip ${!cat ? 'on' : ''}`} onClick={() => setCat('')}>All · {kb.docs.length}</button>
                {cats.map((c) => <button key={c} className={`chip ${cat === c ? 'on' : ''}`} onClick={() => setCat(cat === c ? '' : c)}>{c} · {counts[c]}</button>)}
              </div>
              <table className="tbl"><tbody>
                {shown.map((d, i) => (
                  <Fragment key={d.id}>
                    {(!cat && (i === 0 || shown[i - 1].category !== d.category)) && <tr className="grp"><td colSpan="4">{d.category}</td></tr>}
                    <tr onClick={() => show(d)}>
                      <td className="strong">{d.title}{d.source && d.source !== d.title && <small>{d.source}</small>}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <select value={d.category} style={{ width: 'auto' }} onChange={(e) => setCategory(d, e.target.value)}>{kb.categories.map((c) => <option key={c}>{c}</option>)}</select>
                      </td>
                      <td className="num">{Math.round(d.size / 100) / 10}k</td>
                      <td className="num"><button className="link" onClick={async (e) => { e.stopPropagation(); await api.del(`/kb/${id}/docs/${d.id}`); reload(); onChanged() }}>Delete</button></td>
                    </tr>
                    {open?.id === d.id && <tr><td colSpan="4">
                      {open.source && open.source !== 'imported' && <p className="muted small">Source: <a href={open.source} target="_blank" rel="noreferrer">{open.source}</a></p>}
                      <pre className="docview">{open.body}</pre>
                    </td></tr>}
                  </Fragment>
                ))}
              </tbody></table>
            </>
          )
        })()}
      </Panel>

      <Panel title="How to say names and abbreviations" right={<span className="actions">{pronMsg && <span className="ok small">{pronMsg}</span>}<button className="btn small" onClick={() => setPron([...(pron || []), { term: '', language: 'English', respelling: '' }])}>+ Add</button></span>}>
        <p className="muted small">Voices often mangle abbreviations. Write each one the way it should be <i>spoken</i>, per language. The agent writes it exactly like this, so the voice reads it right. These are first guesses: please check them by ear on a test call.</p>
        <div className="pron">
          {(pron || []).map((r, i) => (
            <div key={i} className="pron-row">
              <input value={r.term} placeholder="e.g. a product or brand name" onChange={(e) => setPron(pron.map((x, j) => (j === i ? { ...x, term: e.target.value } : x)))} />
              <select value={r.language} onChange={(e) => setPron(pron.map((x, j) => (j === i ? { ...x, language: e.target.value } : x)))}>{['English', 'Arabic', 'Hindi', 'Urdu', 'Bengali', 'Chinese', 'Indonesian'].map((l) => <option key={l}>{l}</option>)}</select>
              <input dir="auto" value={r.respelling} placeholder="how to write it" onChange={(e) => setPron(pron.map((x, j) => (j === i ? { ...x, respelling: e.target.value } : x)))} />
              <button className="link" onClick={() => setPron(pron.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
        </div>
        <div className="actions" style={{ marginTop: 10 }}><button className="btn small solid" onClick={async () => { setPron(await api.put(`/kb/${id}/pronunciations`, pron)); setPronMsg('Saved'); setTimeout(() => setPronMsg(''), 1800) }}>Save</button></div>
      </Panel>

      <Panel title="Try it: what would the agent find?">
        <div className="filters" style={{ padding: 0, border: 0 }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && q && test()} placeholder="e.g. price of the standard plan" />
          <button className="btn" disabled={!q} onClick={test}>Search</button>
        </div>
        {hits && hits.map((h, i) => <pre key={i} className="docview">{h}</pre>)}
      </Panel>
    </div>
  )
}

export default function Knowledge() {
  const [sp, setSp] = useSearchParams()
  const [list, reload] = usePoll('/kb', 0)
  const open = sp.get('open') || (list?.[0] && String(list[0].id))
  const create = async () => { const k = await api.post('/kb', { name: 'New knowledge base' }); await reload(); setSp({ open: k.id }) }
  return (
    <div className="page">
      <header className="page-h"><h1>Knowledge</h1><button className="btn solid" onClick={create}>+ New knowledge base</button></header>
      <div className="split clients">
        <Panel pad={false} className="client-list">
          {(list || []).map((k) => (
            <button key={k.id} className={`li ${String(k.id) === open ? 'sel' : ''}`} onClick={() => setSp({ open: k.id })}>
              <b>{k.name}</b><span>{k.docs} documents · {k.campaigns} campaigns</span>
            </button>
          ))}
        </Panel>
        <div>{open ? <Editor key={open} id={open} onChanged={reload} /> : <Empty title="No knowledge bases" />}</div>
      </div>
    </div>
  )
}
