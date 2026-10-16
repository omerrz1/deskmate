import { useEffect, useMemo, useRef, useState } from 'react'
import { api, apiFetch, usePoll } from './api'
import Icon from './Icon'
import { useConfirm, usePrompt, useToast } from './Feedback'
import { useAuth } from './Auth'

const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '')
const size = (n) => (!n ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`)
const KIND = { doc: 'Document', sheet: 'Spreadsheet', chart: 'Chart', file: 'File' }
const iconOf = (f) => (f.kind === 'sheet' ? 'sheet' : f.kind === 'chart' ? 'chart' : f.kind === 'file' ? 'file' : 'doc')
// Where things are, by colour AND by word: person = your own files, shared = the company drive, project = a project's drive, ai = an AI employee's computer
const PLACE_ICON = { person: 'person', shared: 'drive', project: 'projects', own: 'computer', agent: 'computer' }
const PLACE_TONE = { person: 'person', shared: 'company', project: 'project', own: 'ai', agent: 'ai' }
const PLACE_GROUP = { person: 'Mine', shared: 'Company', project: 'Projects', own: 'This computer', agent: 'AI team' }
const PURPOSE_TONE = { admin: 'admin', from_admin: 'from' }  // folders the system keeps: what an employee leaves FOR the admins, and what the admins leave FOR them
const extOf = (f) => (f.kind === 'sheet' ? 'XLS' : f.kind === 'chart' ? 'SVG' : f.kind === 'file' ? (f.title.split('.').pop() || 'FILE').slice(0, 4).toUpperCase() : 'DOC')

/**
 * A file explorer for one drive at a time: an employee's computer, the shared drive or a project drive.
 * Folder tree, icon and list views, search, drag and drop to move, quick preview with editing, copy to another drive.
 * Pass agentId to let people switch between that employee's drives.
 */
export default function FileManager({ owner = 'shared', title, agentId, fill = false, places: placesProp = null }) {
  const { isAdmin } = useAuth()
  const box = useRef(null)
  const fileInput = useRef(null)
  const confirm = useConfirm()
  const prompt = usePrompt()
  const toast = useToast()
  const [narrow, setNarrow] = useState(false)
  const [drives, setDrives] = useState(null)
  const [place, setPlace] = useState(owner)
  const [cur, setCur] = useState(null)
  const [hist, setHist] = useState([])
  const [sel, setSel] = useState(null) // { t: 'f' | 'd', id }
  const [view, setView] = useState(() => { try { return localStorage.getItem('dm-fm-view') || 'grid' } catch { return 'grid' } })
  const [sort, setSort] = useState({ by: 'name', dir: 1 })
  const [q, setQ] = useState('')
  const [results, setResults] = useState(null)
  const [creating, setCreating] = useState(null)
  const [renaming, setRenaming] = useState(null)
  const [menu, setMenu] = useState(null)
  const [open, setOpen] = useState(null)
  const [edit, setEdit] = useState('')
  const [drop, setDrop] = useState(null)
  const [exp, setExp] = useState({})
  const [tree, reloadTree] = usePoll(`/fs/folders?owner=${place}`, 8000, [place])
  const [ls, reload] = usePoll(`/fs?owner=${place}${cur ? `&folder=${cur}` : ''}`, 4000, [place, cur])

  useEffect(() => { setPlace(owner); setCur(null); setHist([]); setSel(null); setOpen(null) }, [owner])
  useEffect(() => {
    if (!agentId) { setDrives(null); return }
    api.get(`/agents/${agentId}/drives`).then(setDrives).catch(() => setDrives(null))
  }, [agentId])
  useEffect(() => {
    if (!box.current || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 740))
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    const t = q.trim()
    if (!t) { setResults(null); return }
    const h = setTimeout(() => api.get(`/fs/search?owner=${place}&q=${encodeURIComponent(t)}`).then(setResults).catch(() => setResults(null)), 250)
    return () => clearTimeout(h)
  }, [q, place])
  useEffect(() => { const close = () => setMenu(null); window.addEventListener('click', close); return () => window.removeEventListener('click', close) }, [])

  const places = placesProp || drives || [{ owner, label: title || (owner === 'shared' ? 'Shared drive' : 'Files'), kind: owner === 'shared' ? 'shared' : 'own' }]
  const placeLabel = (places.find((p) => p.owner === place) || places[0]).label
  const groups = (() => { const m = new Map(); places.forEach((p) => { const g = p.group || PLACE_GROUP[p.kind] || 'Files'; if (!m.has(g)) m.set(g, []); m.get(g).push(p) }); return [...m] })()
  const copyTargets = places.filter((p) => p.owner !== place && p.kind !== 'agent')  // copying onto an AI employee's computer is done from their own Computer
  const tagOf = (purpose) => (purpose === 'admin' ? (isAdmin ? 'For you' : 'For admins') : purpose === 'from_admin' ? (isAdmin ? 'From you' : 'From admins') : '')
  const folders = tree || []
  const byId = useMemo(() => Object.fromEntries(folders.map((f) => [f.id, f])), [folders])
  const chain = useMemo(() => { const out = []; let c = byId[cur]; while (c) { out.unshift(c); c = byId[c.parent_id] } return out }, [byId, cur])
  const kids = useMemo(() => { const m = {}; folders.forEach((f) => { (m[f.parent_id ?? 'root'] ||= []).push(f) }); return m }, [folders])

  const setMsg = (t) => toast.ok(t)
  const fail = (e) => toast.err(e.message || 'That did not work')
  const refresh = () => { reload(); reloadTree() }
  const go = (p, f, push = true) => {
    if (push) setHist((h) => [...h, { place, cur }])
    setPlace(p); setCur(f); setSel(null); setOpen(null); setQ(''); setResults(null); setCreating(null); setRenaming(null)
  }
  const back = () => { const h = hist[hist.length - 1]; if (!h) return; setHist(hist.slice(0, -1)); setPlace(h.place); setCur(h.cur); setSel(null); setOpen(null) }
  const up = () => go(place, byId[cur]?.parent_id ?? null)
  const loadDoc = async (id) => { const d = await api.get(`/docs/${id}`); setOpen(d); setEdit(d.kind === 'doc' ? d.body : '') }
  const pickFile = (f) => { setSel({ t: 'd', id: f.id }); setRenaming(null); loadDoc(f.id).catch(fail) }
  const lastClick = useRef({ id: null, at: 0 })
  const pickFolder = (f) => {
    const n = Date.now()
    if (lastClick.current.id === f.id && n - lastClick.current.at < 500) { lastClick.current = { id: null, at: 0 }; go(place, f.id); return } // a quick second click opens it
    lastClick.current = { id: f.id, at: n }
    setSel({ t: 'f', id: f.id }); setOpen(null)
  }
  const openFolder = (f) => go(place, f.id)

  const create = async (name) => {
    const kind = creating; setCreating(null)
    if (!name.trim()) return
    try {
      if (kind === 'folder') await api.post('/fs/folder', { owner: place, parent_id: cur, name })
      else { const r = await api.post('/fs/file', { owner: place, folder_id: cur, title: name, kind }); await refresh(); pickFile({ id: r.id }); return }
      refresh()
    } catch (e) { fail(e) }
  }
  const rename = async () => {
    const r = renaming; setRenaming(null)
    if (!r || !r.v.trim()) return
    try { if (r.t === 'f') await api.patch(`/fs/folder/${r.id}`, { name: r.v }); else { await api.patch(`/docs/${r.id}`, { title: r.v }); if (open?.id === r.id) setOpen({ ...open, title: r.v }) } refresh() } catch (e) { fail(e) }
  }
  const remove = async (t, id, name) => {
    const ok = await confirm({ title: t === 'f' ? `Delete the folder “${name}”?` : `Delete “${name}”?`, body: t === 'f' ? 'Everything inside it is deleted too.' : 'This cannot be undone.', confirm: 'Delete', danger: true })
    if (!ok) return
    try { await api.del(t === 'f' ? `/fs/folder/${id}` : `/docs/${id}`); if (sel?.id === id) { setSel(null); setOpen(null) } refresh() } catch (e) { fail(e) }
  }
  const move = async (item, target) => {
    try {
      if (item.t === 'f') await api.patch(`/fs/folder/${item.id}`, target === null ? { to_root: true } : { parent_id: target })
      else await api.patch(`/docs/${item.id}`, target === null ? { to_root: true } : { folder_id: target })
      refresh()
    } catch (e) { fail(e) }
  }
  const copyTo = async (id, toOwner, label) => { try { await api.post(`/docs/${id}/copy`, { owner: toOwner, folder_id: null }); setMsg(`Copied to ${label}.`) } catch (e) { fail(e) } }
  const upload = async (list, folder = cur) => {
    for (const f of Array.from(list || [])) {
      const fd = new FormData(); fd.append('owner', place); if (folder) fd.append('folder_id', folder); fd.append('file', f)
      try { const r = await api.form('/fs/upload', fd); setMsg(`Added ${f.name}.`); await refresh(); pickFile({ id: r.id }) } catch (e) { fail(e) }
    }
  }
  const download = async (d) => {
    const r = await apiFetch(`/api/docs/${d.id}/download`); const b = await r.blob()
    const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = d.kind === 'file' ? d.title : `${d.title}.${d.kind === 'sheet' ? 'xlsx' : d.kind === 'chart' ? 'svg' : 'md'}`; a.click()
  }
  const saveTable = async (t) => { setOpen({ ...open, table: t }); try { await api.put(`/docs/${open.id}/table`, t) } catch (e) { fail(e) } }

  // drag and drop: move inside a drive, copy to another drive, or add files dropped from the computer
  const onDragStart = (e, item) => { e.dataTransfer.setData('text/dm', JSON.stringify({ ...item, place })); e.dataTransfer.effectAllowed = 'copyMove' }
  const accept = (key) => ({
    onDragOver: (e) => { e.preventDefault(); setDrop(key) },
    onDragLeave: () => setDrop((d) => (d === key ? null : d)),
  })
  const dropOn = (e, folderId, toPlace = place, label) => {
    e.preventDefault(); e.stopPropagation(); setDrop(null)
    if (e.dataTransfer.files && e.dataTransfer.files.length) { if (toPlace === place) upload(e.dataTransfer.files, folderId); return }
    let item; try { item = JSON.parse(e.dataTransfer.getData('text/dm')) } catch { return }
    if (!item) return
    if (toPlace === place) { if (item.t === 'f' && item.id === folderId) return; move({ t: item.t, id: item.id }, folderId) }
    else if (item.t === 'd') copyTo(item.id, toPlace, label)
    else setMsg('Folders can only move inside one drive.')
  }

  const items = useMemo(() => {
    const dirs = (kids[cur ?? 'root'] || []).map((f) => ({ t: 'f', id: f.id, name: f.name, kind: 'folder', purpose: f.purpose || '' }))
    const docs = (ls?.files || []).map((f) => ({ t: 'd', id: f.id, name: f.title, kind: f.kind, updated_at: f.updated_at, size: f.size, raw: f }))
    const cmp = (a, b) => {
      const k = sort.by
      const av = k === 'modified' ? a.updated_at || 0 : k === 'kind' ? a.kind : k === 'size' ? a.size || 0 : a.name.toLowerCase()
      const bv = k === 'modified' ? b.updated_at || 0 : k === 'kind' ? b.kind : k === 'size' ? b.size || 0 : b.name.toLowerCase()
      return (av > bv ? 1 : av < bv ? -1 : 0) * sort.dir
    }
    return [...dirs.sort((a, b) => (!a.purpose - !b.purpose) || a.name.toLowerCase().localeCompare(b.name.toLowerCase())), ...docs.sort(cmp)]  // the folders for and from the admins come first
  }, [kids, cur, ls, sort])
  const isSel = (it) => sel && sel.t === it.t && sel.id === it.id
  const openMenu = (e, it) => { e.preventDefault(); e.stopPropagation(); if (it) (it.t === 'f' ? pickFolder({ id: it.id }) : pickFile({ id: it.id })); setMenu({ x: e.clientX, y: e.clientY, it }) }
  const t = open?.table

  const nameCell = (it, grid) => (renaming && renaming.t === it.t && renaming.id === it.id
    ? <input className="fx-rename" autoFocus value={renaming.v} onClick={(e) => e.stopPropagation()} onChange={(e) => setRenaming({ ...renaming, v: e.target.value })} onBlur={rename} onKeyDown={(e) => { if (e.key === 'Enter') rename(); if (e.key === 'Escape') setRenaming(null) }} />
    : <span className={grid ? 'fx-name' : 'fx-lname'}>{it.name}</span>)

  const renderTree = (parent, depth) => (kids[parent] || []).map((f) => {
    const open_ = exp[f.id] || chain.some((c) => c.id === f.id)
    const has = !!kids[f.id]
    return (
      <div key={f.id}>
        <div className={`fx-node ${cur === f.id ? 'on' : ''} ${drop === 't' + f.id ? 'drop' : ''}`} style={{ paddingLeft: 8 + depth * 14 }} onClick={() => go(place, f.id)}
          {...accept('t' + f.id)} onDrop={(e) => dropOn(e, f.id)}>
          <button className="fx-tw" onClick={(e) => { e.stopPropagation(); setExp({ ...exp, [f.id]: !open_ }) }} style={{ visibility: has ? 'visible' : 'hidden' }}><Icon name={open_ ? 'chevD' : 'chevR'} size={12} /></button>
          <Icon name="folder" size={15} className={f.purpose ? `fx-fi fx-k-${PURPOSE_TONE[f.purpose]}` : ''} /><span>{f.name}</span>{f.purpose && <em className={`fx-tag fx-k-${PURPOSE_TONE[f.purpose]}`}>{tagOf(f.purpose)}</em>}
        </div>
        {open_ && has && renderTree(f.id, depth + 1)}
      </div>
    )
  })

  const showPreview = sel?.t === 'd' && !!open

  return (
    <div className={`fx ${fill ? 'fill' : ''} ${narrow ? 'narrow' : ''}`} ref={box}
      onKeyDown={(e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
        if (e.key === 'Backspace' || (e.altKey && e.key === 'ArrowUp')) { e.preventDefault(); up() }
        if (e.key === 'Delete' && sel) { const it = items.find((i) => isSel(i)); if (it && !it.purpose) remove(it.t, it.id, it.name) }
        if (e.key === 'Enter' && sel?.t === 'f') openFolder({ id: sel.id })
      }} tabIndex={0}>
      <div className="fx-bar">
        <button className="fx-btn" disabled={!hist.length} onClick={back} title="Back"><Icon name="back" size={16} /></button>
        <button className="fx-btn" disabled={!cur} onClick={up} title="Up one folder"><Icon name="up" size={16} /></button>
        {narrow && places.length > 1 && <select className="fx-place" value={place} onChange={(e) => go(e.target.value, null)}>{places.map((p) => <option key={p.owner} value={p.owner}>{p.label}</option>)}</select>}
        <div className="fx-crumbs">
          <button className={drop === 'root' ? 'drop' : ''} onClick={() => go(place, null)} {...accept('root')} onDrop={(e) => dropOn(e, null)}>{placeLabel}</button>
          {chain.map((c) => <span key={c.id}><Icon name="chevR" size={12} /><button className={drop === 'c' + c.id ? 'drop' : ''} onClick={() => go(place, c.id)} {...accept('c' + c.id)} onDrop={(e) => dropOn(e, c.id)}>{c.name}</button></span>)}
        </div>
        <div className="fx-search"><Icon name="search" size={14} /><input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="fx-seg">
          <button className={view === 'grid' ? 'on' : ''} onClick={() => { setView('grid'); try { localStorage.setItem('dm-fm-view', 'grid') } catch { /* ignore */ } }} title="Icons"><Icon name="grid" size={15} /></button>
          <button className={view === 'list' ? 'on' : ''} onClick={() => { setView('list'); try { localStorage.setItem('dm-fm-view', 'list') } catch { /* ignore */ } }} title="List"><Icon name="list" size={15} /></button>
        </div>
        <button className="btn small" onClick={() => setCreating('folder')}><Icon name="plus" size={13} /> Folder</button>
        <button className="btn small" onClick={() => setCreating('doc')}><Icon name="plus" size={13} /> Document</button>
        <button className="btn small" onClick={() => setCreating('sheet')}><Icon name="plus" size={13} /> Sheet</button>
        <button className="btn small" onClick={() => fileInput.current.click()}><Icon name="upload" size={13} /> Upload</button>
        <input ref={fileInput} type="file" multiple hidden accept=".pdf,.txt,.md,.csv,.xlsx,.xls,.json" onChange={(e) => { upload(e.target.files); e.target.value = '' }} />
      </div>

      <div className="fx-main">
        {!narrow && (
          <aside className="fx-side">
            {groups.map(([group, list]) => (
              <div key={group} className="fx-group">
                {(groups.length > 1 || list.length > 1) && <h5>{group}</h5>}
                {list.map((p) => (
                  <div key={p.owner}>
                    <div className={`fx-place-row fx-k-${PLACE_TONE[p.kind] || 'ai'} ${place === p.owner && !cur ? 'on' : ''} ${drop === 'p' + p.owner ? 'drop' : ''}`} onClick={() => go(p.owner, null)} title={p.hint || ''}
                      {...accept('p' + p.owner)} onDrop={(e) => dropOn(e, null, p.owner, p.label)}>
                      <Icon name={PLACE_ICON[p.kind] || 'folder'} size={15} /><span>{p.label}</span>
                    </div>
                    {place === p.owner && renderTree('root', 0)}
                  </div>
                ))}
              </div>
            ))}
            {places.length > 1 && (
              <div className="fx-legend" aria-label="What the colours mean">
                <h5>Colours</h5>
                {places.some((p) => p.kind === 'agent' || p.kind === 'own') && <><span className="fx-k-admin"><i />Orange: for the admins to read</span><span className="fx-k-from"><i />Teal: what the admins gave them</span></>}
                {places.some((p) => p.kind === 'person') && <span className="fx-k-person"><i />Green: your own files</span>}
                {places.some((p) => p.kind === 'shared') && <span className="fx-k-company"><i />Blue: the company drive</span>}
                {places.some((p) => p.kind === 'project') && <span className="fx-k-project"><i />Purple: a project</span>}
              </div>
            )}
          </aside>
        )}

        <div className={`fx-content ${drop === 'area' ? 'drop' : ''}`} onClick={() => { setSel(null); setOpen(null) }} onContextMenu={(e) => openMenu(e, null)} {...accept('area')} onDrop={(e) => dropOn(e, cur)}>
          {results ? (
            <div className="fx-results">
              <p className="muted small" style={{ padding: '8px 12px' }}>{results.folders.length + results.files.length} result{results.folders.length + results.files.length === 1 ? '' : 's'} for “{q}”</p>
              {results.folders.map((f) => <button key={'rf' + f.id} className="fx-row" onClick={(e) => { e.stopPropagation(); go(place, f.id) }}><Icon name="folder" size={16} /><span className="fx-lname">{f.name}</span><span className="muted small">{f.path}</span></button>)}
              {results.files.map((f) => <button key={'rd' + f.id} className="fx-row" onClick={(e) => { e.stopPropagation(); go(place, f.folder_id); setTimeout(() => pickFile(f), 50) }}><Icon name={iconOf(f)} size={16} /><span className="fx-lname">{f.title}</span><span className="muted small">{f.path || '/'} · {when(f.updated_at)}</span></button>)}
            </div>
          ) : view === 'grid' ? (
            <div className="fx-grid">
              {creating && (
                <div className="fx-tile" onClick={(e) => e.stopPropagation()}>
                  <div className="fx-ico"><Icon name={creating === 'folder' ? 'folder' : creating === 'sheet' ? 'sheet' : 'doc'} size={30} /></div>
                  <input className="fx-rename" autoFocus placeholder={creating === 'folder' ? 'Folder name' : 'Name'} onKeyDown={(e) => { if (e.key === 'Enter') create(e.target.value); if (e.key === 'Escape') setCreating(null) }} onBlur={(e) => create(e.target.value)} />
                </div>
              )}
              {items.map((it) => (
                <div key={it.t + it.id} className={`fx-tile ${isSel(it) ? 'on' : ''} ${drop === 'f' + it.id && it.t === 'f' ? 'drop' : ''}`} draggable onDragStart={(e) => onDragStart(e, { t: it.t, id: it.id })}
                  onClick={(e) => { e.stopPropagation(); it.t === 'f' ? pickFolder(it) : pickFile(it.raw) }} onDoubleClick={() => it.t === 'f' && openFolder(it)} onContextMenu={(e) => openMenu(e, it)}
                  {...(it.t === 'f' ? { ...accept('f' + it.id), onDrop: (e) => dropOn(e, it.id) } : {})}>
                  <div className={`fx-ico ${it.t === 'f' ? 'dir' : ''} ${it.purpose ? `fx-k-${PURPOSE_TONE[it.purpose]}` : ''}`}><Icon name={it.t === 'f' ? 'folder' : iconOf(it.raw)} size={30} />{it.t === 'd' && <em>{extOf(it.raw)}</em>}</div>
                  {nameCell(it, true)}{it.purpose && <span className={`fx-tag fx-k-${PURPOSE_TONE[it.purpose]}`}>{tagOf(it.purpose)}</span>}
                </div>
              ))}
            </div>
          ) : (
            <div className="fx-list">
              <div className="fx-head">
                <span />{[['name', 'Name'], ['kind', 'Kind'], ['modified', 'Modified'], ['size', 'Size']].map(([k, l]) => <button key={k} className={k === 'name' ? 'n' : ''} onClick={(e) => { e.stopPropagation(); setSort({ by: k, dir: sort.by === k ? -sort.dir : 1 }) }}>{l}{sort.by === k ? (sort.dir > 0 ? ' ↑' : ' ↓') : ''}</button>)}
              </div>
              {creating && <div className="fx-row" onClick={(e) => e.stopPropagation()}><Icon name={creating === 'folder' ? 'folder' : creating === 'sheet' ? 'sheet' : 'doc'} size={16} /><input className="fx-rename" autoFocus placeholder="Name" onKeyDown={(e) => { if (e.key === 'Enter') create(e.target.value); if (e.key === 'Escape') setCreating(null) }} onBlur={(e) => create(e.target.value)} /></div>}
              {items.map((it) => (
                <div key={it.t + it.id} className={`fx-row ${isSel(it) ? 'on' : ''} ${drop === 'f' + it.id && it.t === 'f' ? 'drop' : ''}`} draggable onDragStart={(e) => onDragStart(e, { t: it.t, id: it.id })}
                  onClick={(e) => { e.stopPropagation(); it.t === 'f' ? pickFolder(it) : pickFile(it.raw) }} onDoubleClick={() => it.t === 'f' && openFolder(it)} onContextMenu={(e) => openMenu(e, it)}
                  {...(it.t === 'f' ? { ...accept('f' + it.id), onDrop: (e) => dropOn(e, it.id) } : {})}>
                  <Icon name={it.t === 'f' ? 'folder' : iconOf(it.raw)} size={16} className={it.purpose ? `fx-fi fx-k-${PURPOSE_TONE[it.purpose]}` : ''} />{nameCell(it, false)}
                  <span className="fx-c">{it.t === 'f' ? (it.purpose ? <em className={`fx-tag fx-k-${PURPOSE_TONE[it.purpose]}`}>{tagOf(it.purpose)}</em> : 'Folder') : KIND[it.kind] || 'File'}</span><span className="fx-c">{it.t === 'f' ? '' : when(it.updated_at)}</span><span className="fx-c r">{it.t === 'f' ? '' : size(it.size)}</span>
                </div>
              ))}
            </div>
          )}
          {!results && ls && items.length === 0 && !creating && <div className="fx-empty">This folder is empty.<br /><span className="small">Drop files here, or use the buttons above.</span></div>}
        </div>

        {showPreview && (
          <aside className="fx-prev" onClick={(e) => e.stopPropagation()}>
            {open && (
              <>
                <div className="fx-pt"><Icon name={iconOf(open)} size={18} />
                  {renaming && renaming.t === 'd' && renaming.id === open.id
                    ? <input className="fx-rename" autoFocus value={renaming.v} onChange={(e) => setRenaming({ ...renaming, v: e.target.value })} onBlur={rename} onKeyDown={(e) => { if (e.key === 'Enter') rename(); if (e.key === 'Escape') setRenaming(null) }} />
                    : <b onDoubleClick={() => setRenaming({ t: 'd', id: open.id, v: open.title })}>{open.title}</b>}
                </div>
                <p className="muted small">{KIND[open.kind] || 'File'}{size(open.size || (open.body || '').length) ? ` · ${size(open.size || (open.body || '').length)}` : ''} · {open.author ? `by ${open.author}` : 'by you'} · {when(open.updated_at)}</p>
                <div className="fx-pbody">
                  {open.kind === 'sheet' && t ? (
                    <>
                      <div className="sheet">
                        <table>
                          <thead><tr><th />{(t.columns || []).map((c, i) => <th key={i}><input value={c} onChange={(e) => setOpen({ ...open, table: { ...t, columns: t.columns.map((x, j) => (j === i ? e.target.value : x)) } })} onBlur={() => saveTable(open.table)} /></th>)}</tr></thead>
                          <tbody>{(t.rows || []).map((r, i) => (
                            <tr key={i}><td className="rn">{i + 1}</td>{t.columns.map((_, j) => <td key={j}><input value={r[j] ?? ''} onChange={(e) => setOpen({ ...open, table: { ...t, rows: t.rows.map((x, k) => (k === i ? t.columns.map((_, c) => (c === j ? e.target.value : x[c] ?? '')) : x)) } })} onBlur={() => saveTable(open.table)} /></td>)}
                              <td><button className="link" onClick={() => saveTable({ ...t, rows: t.rows.filter((_, k) => k !== i) })}>✕</button></td></tr>
                          ))}</tbody>
                        </table>
                      </div>
                      <div className="actions" style={{ marginTop: 8 }}>
                        <button className="btn small" onClick={() => saveTable({ ...t, rows: [...t.rows, t.columns.map(() => '')] })}>Add row</button>
                        <button className="btn small" onClick={async () => { const n = await prompt({ title: 'Column name', confirm: 'Add' }); if (n) saveTable({ columns: [...t.columns, n], rows: t.rows.map((r) => [...r, '']) }) }}>Add column</button>
                      </div>
                    </>
                  ) : open.kind === 'chart' ? (
                    <img alt={open.title} style={{ maxWidth: '100%', border: '1px solid var(--line)', borderRadius: 8 }} src={`data:image/svg+xml;utf8,${encodeURIComponent(open.body)}`} />
                  ) : open.kind === 'file' ? (
                    <p className="muted small">An exported file. Download it to open it.</p>
                  ) : <textarea className="fx-text" value={edit} onChange={(e) => setEdit(e.target.value)} />}
                </div>
                <div className="actions">
                  {open.kind === 'doc' && <button className="btn solid small" disabled={edit === open.body} onClick={async () => { try { await api.patch(`/docs/${open.id}`, { body: edit }); setOpen({ ...open, body: edit }); setMsg('Saved.'); refresh() } catch (e) { fail(e) } }}>Save</button>}
                  <button className="btn small" onClick={() => download(open)}><Icon name="download" size={13} /> Download</button>
                  <button className="btn small" onClick={() => setRenaming({ t: 'd', id: open.id, v: open.title })}>Rename</button>
                  <button className="btn small danger" onClick={() => remove('d', open.id, open.title)}>Delete</button>
                </div>
                {copyTargets.length > 0 && (
                  <div className="fx-copy"><span className="muted small">Copy to</span>{copyTargets.map((p) => <button key={p.owner} className="chip" onClick={() => copyTo(open.id, p.owner, p.label)}>{p.label}</button>)}</div>
                )}
              </>
            )}
          </aside>
        )}
      </div>

      {menu && (
        <div className="fx-menu" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
          {menu.it ? (
            <>
              {menu.it.t === 'f' && <button onClick={() => { openFolder(menu.it); setMenu(null) }}>Open</button>}
              {!menu.it.purpose && <button onClick={() => { setRenaming({ t: menu.it.t, id: menu.it.id, v: menu.it.name }); setMenu(null) }}>Rename</button>}
              {menu.it.t === 'd' && <button onClick={() => { download({ id: menu.it.id, title: menu.it.name, kind: menu.it.kind }); setMenu(null) }}>Download</button>}
              {menu.it.t === 'd' && copyTargets.map((p) => <button key={p.owner} onClick={() => { copyTo(menu.it.id, p.owner, p.label); setMenu(null) }}>Copy to {p.label}</button>)}
              {cur && !menu.it.purpose && <button onClick={() => { move({ t: menu.it.t, id: menu.it.id }, byId[cur]?.parent_id ?? null); setMenu(null) }}>Move up a folder</button>}
              {!menu.it.purpose && <button className="bad" onClick={() => { remove(menu.it.t, menu.it.id, menu.it.name); setMenu(null) }}>Delete</button>}
              {menu.it.purpose && <span className="fx-note">Deskmates keeps this folder in place.</span>}
            </>
          ) : (
            <>
              <button onClick={() => { setCreating('folder'); setMenu(null) }}>New folder</button>
              <button onClick={() => { setCreating('doc'); setMenu(null) }}>New document</button>
              <button onClick={() => { setCreating('sheet'); setMenu(null) }}>New spreadsheet</button>
              <button onClick={() => { fileInput.current.click(); setMenu(null) }}>Upload files</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
