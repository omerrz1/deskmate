import { useState } from 'react'
import { usePoll, clock, ago } from '../api'
import { Panel, Badge, Empty } from '../ui'

const KINDS = ['', 'call', 'campaign', 'agent', 'dataset', 'report', 'contact', 'error']

export default function Activity() {
  const [kind, setKind] = useState('')
  const [rows] = usePoll(`/activity?limit=200${kind ? `&kind=${kind}` : ''}`, 3000, [kind])
  return (
    <div className="page">
      <header className="page-h"><h1>Activity log</h1></header>
      <div className="chips">
        {KINDS.map((k) => <button key={k} className={`chip ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>{k || 'all'}</button>)}
      </div>
      <Panel pad={false}>
        {rows?.length ? (
          <table className="tbl">
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td className="num" title={clock(a.ts)}>{ago(a.ts)}</td>
                  <td><Badge>{a.kind}</Badge></td>
                  <td className="wide">{a.message}{a.meta?.args && <small className="mono">{JSON.stringify(a.meta.args)}</small>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty title="No activity" hint="Everything the agent and admin do shows up here." />}
      </Panel>
    </div>
  )
}
