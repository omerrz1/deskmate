import { useEffect, useState } from 'react'
import { api, usePoll } from '../../api'
import { useToast } from '../../Feedback'

/** Rules that hold an outgoing email for the admin's approval, whatever the general email policy is. */
export default function Safety() {
  const toast = useToast()
  const [d, reload] = usePoll('/safety/rules', 0)
  const [rules, setRules] = useState([])
  const [add, setAdd] = useState({ type: 'recipients_over', value: '5' })
  const [dirty, setDirty] = useState(false)
  useEffect(() => { if (d) { setRules(d.rules); setDirty(false) } }, [d])
  if (!d) return null
  const set = (next) => { setRules(next); setDirty(true) }
  const save = async () => { try { await api.put('/safety/rules', { rules }); reload(); toast.ok('Rules saved') } catch (e) { toast.err(e.message) } }
  const hint = { recipients_over: 'e.g. 5', words: 'e.g. refund, wire transfer, password', domain_not_in: 'e.g. mycompany.com', attachments: '' }
  return (
    <section className="panel"><div className="panel-b form-grid">
      <h3>Email rules</h3>
      <p className="muted small">An email that matches any rule waits in Inbox → To approve with the reason shown, even if emails are set to send straight away. Rules apply to every employee. You can also require approval for one employee in their settings.</p>
      {rules.length === 0 && <p className="faint">No rules yet.</p>}
      {rules.map((r, i) => (
        <div key={r.id} className="rule-row">
          <label className="check"><input type="checkbox" checked={r.on} onChange={(e) => set(rules.map((x, j) => (j === i ? { ...x, on: e.target.checked } : x)))} /> <b>{d.types[r.type]}</b></label>
          {r.type !== 'attachments' && <input value={r.value} onChange={(e) => set(rules.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />}
          <button className="link danger" onClick={() => set(rules.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <div className="rule-row add">
        <select value={add.type} onChange={(e) => setAdd({ type: e.target.value, value: '' })}>{Object.entries(d.types).map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select>
        {add.type !== 'attachments' && <input placeholder={hint[add.type]} value={add.value} onChange={(e) => setAdd({ ...add, value: e.target.value })} />}
        <button className="btn" onClick={() => { set([...rules, { type: add.type, value: add.value, on: true }]); setAdd({ type: add.type, value: '' }) }}>Add rule</button>
      </div>
      <div className="actions"><button className="btn solid" disabled={!dirty} onClick={save}>Save rules</button></div>
    </div></section>
  )
}
