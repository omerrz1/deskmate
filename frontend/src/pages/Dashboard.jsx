import { Link, useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { api, usePoll, dur } from '../api'
import { Panel, Stat, Bars, Badge, outcomeKind } from '../ui'
import { when } from './Scheduled'

export default function Dashboard({ health }) {
  const [s, reload] = usePoll('/stats', 4000)
  const [calls] = usePoll('/calls?limit=6', 5000)
  const [upcoming] = usePoll('/calls/scheduled', 8000)
  const nav = useNavigate()
  if (!s) return <div className="page"><p className="muted">Loading…</p></div>


  return (
    <div className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">{new Date().getHours() < 12 ? 'Good morning' : new Date().getHours() < 18 ? 'Good afternoon' : 'Good evening'}</p>
          <h1>{s.contacts === 0 ? 'Add customers and your agent is ready to call.' : `${s.calls_today} calls today, ${s.success_rate}% successful.`}</h1>
          <div className="actions"><button className="btn solid" onClick={() => nav('/campaigns?new=1')}>+ New campaign</button></div>
        </div>
      </section>

      {health && !health.openai && <div className="notice">Add your <code>OPENAI_API_KEY</code> to <code>backend/.env</code> and restart the backend.</div>}

      {s.contacts === 0 ? (
        <Panel className="start">
          <h2>Let's get started</h2>
          <ol>
            <li><b>Add your customers</b><span>Upload an Excel or CSV sheet.</span></li>
            <li><b>Create a campaign</b><span>Say who to call and what you want to achieve.</span></li>
            <li><b>Watch the results</b><span>Summaries, transcripts and reports appear here.</span></li>
          </ol>
          <div className="actions">
            <Link className="btn solid" to="/data">Upload Excel</Link>
          </div>
        </Panel>
      ) : (
        <>
          <div className="stats">
            <Stat k="Customers" value={s.contacts} />
            <Stat k="Calls today" value={s.calls_today} />
            <Stat k="Answered" value={s.answer_rate} suffix="%" />
            <Stat k="Successful" value={s.success_rate} suffix="%" />
          </div>
          <div className="grid g-2-1">
            <Panel title="Calls, last 14 days" right={<span className="key"><i className="k1" />answered<i className="k2" />missed</span>}><Bars data={s.by_day} /></Panel>
            <div className="stack">
            <Panel title="Coming up" right={<Link className="link" to="/calls?tab=scheduled">All</Link>}>
              {upcoming?.length ? upcoming.slice(0, 4).map((c) => (
                <Link key={c.id} className="row-link" to="/calls?tab=scheduled"><b dir="auto">{c.contact_name}</b><span>{c.due ? 'due now' : when(c.not_before)} · {c.reason}</span></Link>
              )) : <p className="muted">No calls scheduled.</p>}
            </Panel>
            <Panel title="Needs a callback">
              {s.callbacks.length ? s.callbacks.map((c) => (
                <Link key={c.call_id} className="row-link" to={`/clients?open=${c.contact_id}`}><b>{c.name}</b><span>{c.follow_up || 'Callback'}</span></Link>
              )) : <p className="muted">Nobody right now.</p>}
            </Panel>
            </div>
          </div>
          <Panel title="Recent calls" right={<Link className="link" to="/calls">See all</Link>} pad={false}>
            {calls?.length ? (
              <table className="tbl"><tbody>
                {calls.map((c) => (
                  <tr key={c.id} onClick={() => nav(`/calls?open=${c.id}`)}>
                    <td className="strong" dir="auto">{c.contact_name}</td>
                    <td className="wide">{c.summary || '…'}</td>
                    <td><Badge kind={outcomeKind(c.outcome || c.status)}>{c.outcome || c.status}</Badge></td>
                    <td className="num">{dur(c.duration)}</td>
                  </tr>
                ))}
              </tbody></table>
            ) : <p className="muted pad">No calls yet. Start a campaign.</p>}
          </Panel>
        </>
      )}
    </div>
  )
}
