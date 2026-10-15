import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { usePoll, dur, clock, label } from '../api'
import { Panel, Badge, Empty, outcomeKind } from '../ui'
import Scheduled from './Scheduled'

function Transcript({ call }) {
  return (
    <div className="rec-player">
      <p className="muted small" style={{ marginBottom: 8 }}>Calls are not recorded. This is the written transcript.</p>
      <div className="transcript">
        {call.transcript.length === 0 && <p className="muted small">Nothing was said yet.</p>}
        {call.transcript.map((t, i) => (
          <div key={i} className={`line ${t.role}`}>
            <small>{t.role === 'agent' ? 'Our caller' : 'Customer'} · {dur(t.t)}</small>
            <p dir="auto">{t.text}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function Detail({ id, onClose }) {
  const [c] = usePoll(`/calls/${id}`, 2500, [id])
  if (!c) return <aside className="drawer"><p className="muted">Loading…</p></aside>
  const live = c.status === 'in_progress' || c.status === 'ringing'
  return (
    <aside className="drawer">
      <div className="drawer-h">
        <div><p className="eyebrow">Call #{c.id} · try {c.attempt}</p>
          <h2 dir="auto">{c.contact_name}</h2>
          <small className="mono">{c.contact_phone} · {c.contact_language}</small></div>
        <button className="link" onClick={onClose}>Close ✕</button>
      </div>
      <div className="chips">
        {live && <span className="chip static"><i className="dot live" /> {label(c.status)}</span>}
        <Badge kind={outcomeKind(c.outcome || c.status)}>{c.outcome || c.status}</Badge>
        {c.sentiment && <Badge>{c.sentiment}</Badge>}
        <span className="chip static">{dur(c.duration)}</span>
        <span className="chip static">{clock(c.created_at)}</span>
      </div>
      {c.campaign_name && <p className="muted small">Campaign: {c.campaign_name} — {c.objective}</p>}
      <Panel title="Summary">
        <p>{c.summary || (live ? 'Summary is generated when the call ends.' : '—')}</p>
        {c.key_points?.length > 0 && <ul className="points">{c.key_points.map((k, n) => <li key={n}>{k}</li>)}</ul>}
        {c.follow_up && <p className="follow"><b>Next step:</b> {c.follow_up}</p>}
        <Link className="link" to={`/clients?open=${c.contact_id}`}>Client report →</Link>
      </Panel>
      {c.actions?.length > 0 && (
        <Panel title="What the agent did">
          <ul className="points">
            {c.actions.map((x) => (
              <li key={x.id}><b>{ACTIONS[x.kind] || x.kind}</b>{x.status === 'pending' ? ' · needs your team' : ''} <span className="muted small">{Object.values(x.detail || {}).filter(Boolean).join(' · ')}</span></li>
            ))}
          </ul>
        </Panel>
      )}
      <Panel title="Transcript"><Transcript key={c.id + ':' + c.transcript.length + c.status} call={c} /></Panel>
    </aside>
  )
}

const ACTIONS = { callback: 'Booked a callback', update_contact: 'Saved customer details', interest: 'Logged interest', send_information: 'Asked to send information',
  request_human: 'Asked for a person to follow up', appointment: 'Requested an appointment', do_not_call: 'Marked do not call', wrong_number: 'Wrong number',
  flag: 'Flagged for review', switch_language: 'Switched language' }

export default function Calls() {
  const [sp, setSp] = useSearchParams()
  const campaign = sp.get('campaign')
  const qs = `/calls?limit=150${campaign ? `&campaign_id=${campaign}` : ''}`
  const [list] = usePoll(qs, 3000, [qs])
  const open = sp.get('open')
  const tab = sp.get('tab')
  const setOpen = (id) => { const n = new URLSearchParams(sp); if (id) n.set('open', id); else n.delete('open'); setSp(n) }

  return (
    <div className={`page ${open ? 'with-drawer' : ''}`}>
      <header className="page-h">
        <h1>Calls</h1>
        <div className="chips" style={{ margin: 0 }}>
          <button className={`chip ${tab !== 'scheduled' ? 'on' : ''}`} onClick={() => { const n = new URLSearchParams(sp); n.delete('tab'); setSp(n) }}>History</button>
          <button className={`chip ${tab === 'scheduled' ? 'on' : ''}`} onClick={() => { const n = new URLSearchParams(sp); n.set('tab', 'scheduled'); n.delete('open'); setSp(n) }}>Scheduled</button>
        </div>
        {campaign && <button className="chip on" onClick={() => { const n = new URLSearchParams(sp); n.delete('campaign'); setSp(n) }}>Campaign #{campaign} ✕</button>}
      </header>
      {tab === 'scheduled' ? <Scheduled /> : <div className="split">
        <Panel pad={false}>
          {list?.length ? (
            <div className="scroll-x"><table className="tbl">
              
              <tbody>
                {list.map((c) => (
                  <tr key={c.id} className={String(c.id) === open ? 'sel' : ''} onClick={() => setOpen(c.id)}>
                    <td className="strong" dir="auto">{c.contact_name}<small>{c.campaign_name || 'Ad-hoc'} · try {c.attempt}</small></td>
                    <td><Badge kind={outcomeKind(c.outcome || c.status)}>{(c.status === 'in_progress' ? 'live' : c.outcome || c.status)}</Badge></td>
                    <td className="wide clip">{c.summary}</td>
                    <td className="num">{clock(c.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          ) : <Empty title="No calls match" hint="Start a campaign to see calls appear here in real time." />}
        </Panel>
        {open && <Detail id={open} onClose={() => setOpen(null)} />}
      </div>}
    </div>
  )
}
