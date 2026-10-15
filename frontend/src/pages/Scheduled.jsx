import { useState } from 'react'
import { api, usePoll } from '../api'
import { Panel, Badge, Empty } from '../ui'

export const when = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const toLocalInput = (ts) => { const d = new Date(ts * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16) }

export default function Scheduled() {
  const [rows, reload] = usePoll('/calls/scheduled', 5000)
  const [edit, setEdit] = useState(null)
  const act = async (fn) => { await fn(); reload() }

  if (rows && !rows.length) return <Panel><Empty title="Nothing scheduled" hint="Retries, callbacks and follow-ups appear here automatically. You can also schedule a call from a customer's page." /></Panel>
  return (
    <Panel pad={false}>
      <table className="tbl"><tbody>
        {(rows || []).map((r) => (
          <tr key={r.id} style={{ cursor: 'default' }}>
            <td className="strong" dir="auto">{r.contact_name}<small>{r.campaign_name || 'Ad-hoc'} · try {r.attempt}</small></td>
            <td><Badge>{r.reason || 'queued'}</Badge></td>
            <td>
              {edit?.id === r.id ? (
                <span className="actions">
                  <input type="datetime-local" style={{ width: 'auto' }} value={edit.v} onChange={(e) => setEdit({ ...edit, v: e.target.value })} />
                  <button className="btn small solid" onClick={() => act(async () => { await api.post(`/calls/${r.id}/reschedule`, { at: new Date(edit.v).getTime() / 1000 }); setEdit(null) })}>Save</button>
                  <button className="link" onClick={() => setEdit(null)}>Cancel</button>
                </span>
              ) : r.due ? <span>{r.waiting_for ? `Waiting for ${r.waiting_for}` : 'Due now'}</span> : <span>{when(r.not_before)}</span>}
            </td>
            <td className="num">
              <button className="link" onClick={() => act(() => api.post(`/calls/${r.id}/now`))}>Call now</button>
              <button className="link" onClick={() => setEdit({ id: r.id, v: toLocalInput(r.not_before) })}>Reschedule</button>
              <button className="link" onClick={() => act(() => api.post(`/calls/${r.id}/cancel`))}>Cancel</button>
            </td>
          </tr>
        ))}
      </tbody></table>
    </Panel>
  )
}
