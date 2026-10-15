import { useState } from 'react'
import { Link } from 'react-router-dom'
import { usePoll } from '../api'
import Avatar from '../Avatar'
import { EmptyState, PageHeader, Skeleton, useTitle } from '../ui'

const usd = (x) => (x == null ? '–' : x >= 0.1 ? `$${x.toFixed(2)}` : `$${x.toFixed(4)}`)
const mins = (m) => (m == null ? '–' : m < 1 ? `${Math.round(m * 60)} sec` : m < 90 ? `${m.toFixed(1)} min` : `${(m / 60).toFixed(1)} h`)

function Bars({ data }) {
  const max = Math.max(1, ...data)
  return (
    <svg className="sc-bars" viewBox={`0 0 ${data.length * 12} 30`} preserveAspectRatio="none" role="img" aria-label={`Tasks finished per day: ${data.join(', ')}`}>
      {data.map((n, i) => { const h = Math.max(2, (n / max) * 28); return <rect key={i} x={i * 12 + 1} y={30 - h} width="9" height={h} rx="2" className={i === data.length - 1 ? 'now' : ''} /> })}
    </svg>
  )
}

/** How each employee is doing: what they finished, how long it took, how often something failed, and what it cost. */
export default function Scorecards() {
  useTitle('Performance')
  const [days, setDays] = useState(30)
  const [d] = usePoll(`/scorecards?days=${days}`, 20000, [days])
  return (
    <div className="page wide">
      <PageHeader back="Team" title="Performance" sub="What each employee finished, how long it took, how often something went wrong, and what it cost." actions={
        <div className="seg" role="tablist">{[[7, '7 days'], [30, '30 days'], [90, '90 days']].map(([n, l]) => <button key={n} className={days === n ? 'on' : ''} role="tab" aria-selected={days === n} onClick={() => setDays(n)}>{l}</button>)}</div>
      } />
      {!d ? <Skeleton rows={4} h={120} /> : d.cards.length === 0 ? <EmptyState title="No employees yet" hint="Scorecards appear once your team has been working for a while." /> : (
        <>
          <section className="pulse" aria-label="Team totals">
            <div className="pcell"><span className="k">Tasks finished</span><b>{d.total.done}</b><span className="s">in {d.days} days</span></div>
            <div className="pcell"><span className="k">Stuck now</span><b style={d.total.stuck ? { color: 'var(--bad)' } : undefined}>{d.total.stuck}</b><span className="s">tried 3 times, still open</span></div>
            <div className="pcell"><span className="k">Actions that failed</span><b>{d.total.failed}</b><span className="s">tool calls that returned an error</span></div>
            <div className="pcell"><span className="k">AI spend (estimate)</span><b>{usd(d.total.spend)}</b><span className="s">{d.total.emails_sent} emails · {d.total.calls} calls</span></div>
          </section>
          <div className="sc-grid">
            {d.cards.map((c) => (
              <Link key={c.id} to={`/agents/${c.id}`} className="sc-card">
                <div className="sc-head"><Avatar agent={c} size={44} /><div><b>{c.name}</b><span>{c.role || 'Team member'}{c.is_manager ? ' · Manager' : ''}</span></div></div>
                <div className="sc-main"><div><b className="sc-big">{c.done}</b><span>tasks finished</span></div><Bars data={c.series} /></div>
                <dl className="sc-stats">
                  <div><dt>Average time per task</dt><dd>{mins(c.avg_minutes)}</dd></div>
                  <div><dt>Finished by the deadline</dt><dd>{c.on_time == null ? 'no deadlines' : `${c.on_time}% of ${c.due_total}`}</dd></div>
                  <div><dt>Failed actions</dt><dd className={c.failure_rate > 10 ? 'warn-t' : ''}>{c.failure_rate == null ? '–' : `${c.failure_rate}% (${c.failed} of ${c.actions})`}</dd></div>
                  <div><dt>Open / stuck</dt><dd className={c.stuck ? 'warn-t' : ''}>{c.open} open{c.stuck ? ` · ${c.stuck} stuck` : ''}</dd></div>
                  <div><dt>Emails sent</dt><dd>{c.emails_sent}{c.emails_rejected ? ` (${c.emails_rejected} rejected)` : ''}</dd></div>
                  <div><dt>Calls</dt><dd>{c.calls ? `${c.calls}${c.calls_avg_seconds ? ` · avg ${c.calls_avg_seconds}s` : ''}` : '–'}</dd></div>
                  <div><dt>Meetings</dt><dd>{c.meetings}</dd></div>
                  <div><dt>Cost</dt><dd>{usd(c.spend)}{c.cost_per_task ? ` · ${usd(c.cost_per_task)} per task` : ''}</dd></div>
                </dl>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
