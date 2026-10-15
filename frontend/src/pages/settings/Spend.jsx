import { useState } from 'react'
import { api, usePoll } from '../../api'
import { useToast } from '../../Feedback'

const usd = (x) => (x >= 0.1 ? `$${x.toFixed(2)}` : `$${x.toFixed(4)}`)
const PURPOSE = { work: 'Background work', meeting: 'Meetings and chats', 'meeting-voice': 'Voice in meetings', call: 'Phone calls', speech: 'Speech', chat: 'Chat', other: 'Everything else' }

/** What the AI costs, per day and per employee, and the daily caps that pause work when reached. */
export default function Spend() {
  const toast = useToast()
  const [c, reload] = usePoll('/costs?days=14', 15000)
  const [caps, setCaps] = useState({})
  if (!c) return null
  const max = Math.max(0.0001, ...c.by_day.map((d) => d.usd))
  const val = (k) => (caps[k] ?? c.caps[k] ?? 0)
  const saveCaps = async () => { try { await api.put('/costs/caps', { company_day: +val('company_day') || 0, agent_day: +val('agent_day') || 0 }); setCaps({}); reload(); toast.ok('Caps saved') } catch (e) { toast.err(e.message) } }
  const setAgentCap = async (a, v) => { try { await api.patch(`/agents/${a.id}`, { spend_cap_day: +v || 0 }); reload() } catch (e) { toast.err(e.message) } }
  return (
    <>
      <section className="pulse" aria-label="AI spend">
        <div className="pcell"><span className="k">Today</span><b>{usd(c.today)}</b><span className="s">{c.caps.company_day ? `of ${usd(c.caps.company_day)} cap` : 'no daily cap'}</span></div>
        <div className="pcell"><span className="k">Last 7 days</span><b>{usd(c.week)}</b><span className="s">estimate</span></div>
        <div className="pcell"><span className="k">Last {c.days} days</span><b>{usd(c.period)}</b><span className="s">{Math.round(c.tokens / 1000).toLocaleString()}k tokens</span></div>
        <div className="pcell"><span className="k">Per day on average</span><b>{usd(c.period / c.days)}</b><span className="s">over {c.days} days</span></div>
      </section>
      <section className="panel"><div className="panel-b">
        <h3>Daily spend</h3>
        <svg className="spend-chart" viewBox={`0 0 ${c.by_day.length * 30} 110`} role="img" aria-label="Spend per day for the last two weeks">
          {c.by_day.map((d, i) => { const h = Math.max(2, (d.usd / max) * 80); return <g key={i}><rect x={i * 30 + 4} y={90 - h} width="22" height={h} rx="4" className={i === c.by_day.length - 1 ? 'now' : ''}><title>{d.day}: {usd(d.usd)}</title></rect><text x={i * 30 + 15} y="106" textAnchor="middle">{d.day.split(' ')[1]}</text></g> })}
        </svg>
      </div></section>
      <section className="panel"><div className="panel-b form-grid">
        <h3>Spend caps</h3>
        <p className="muted small">When a cap is reached the team stops picking up new work for the rest of the day, and you get an alert. Your own chats and meetings keep working. 0 means no cap.</p>
        <div className="row2">
          <label className="f">Whole company, per day (USD)<input type="number" min="0" step="0.5" value={val('company_day')} onChange={(e) => setCaps({ ...caps, company_day: e.target.value })} /></label>
          <label className="f">Each employee, per day (USD)<input type="number" min="0" step="0.5" value={val('agent_day')} onChange={(e) => setCaps({ ...caps, agent_day: e.target.value })} /></label>
        </div>
        <div className="actions"><button className="btn solid" disabled={!Object.keys(caps).length} onClick={saveCaps}>Save caps</button></div>
      </div></section>
      <section className="panel"><div className="panel-b">
        <h3>By employee</h3>
        <table className="tbl"><thead><tr><td>Employee</td><td className="num">Today</td><td className="num">Own cap (USD/day)</td></tr></thead><tbody>
          {c.agents.map((a) => <tr key={a.id}><td>{a.name}</td><td className="num">{usd(a.today)}</td><td className="num"><input type="number" min="0" step="0.5" defaultValue={a.cap || ''} placeholder="default" style={{ width: 100 }} onBlur={(e) => { if ((+e.target.value || 0) !== (a.cap || 0)) setAgentCap(a, e.target.value) }} /></td></tr>)}
        </tbody></table>
        <h3 style={{ marginTop: 18 }}>By kind of work</h3>
        <ul className="plain">{c.by_purpose.map(([k, v]) => <li key={k} className="row-split"><span>{PURPOSE[k] || k}</span><b>{usd(v)}</b></li>)}</ul>
        <p className="faint small" style={{ marginTop: 10 }}>{c.note}</p>
      </div></section>
    </>
  )
}
