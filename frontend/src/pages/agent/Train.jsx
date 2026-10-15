import { useEffect, useState } from 'react'
import { api } from '../../api'

const STEPS = [['task', 'The task'], ['teach', 'Teach it'], ['review', 'Check the steps'], ['practice', 'Practice'], ['approve', 'Approve']]
const FIELDS = [
  ['when_to_use', 'When do they do this?', 'One sentence. e.g. "When someone applied online but hasn\'t replied for 3 days."', 2],
  ['inputs', 'What do they need first?', 'Information or access before starting. e.g. "The customer\'s name, phone number and what they asked about."', 2],
  ['steps', 'The steps', 'Numbered and concrete. Name the tool for each step: phone call, email, spreadsheet, document, calendar.', 9],
  ['good_result', 'What does a good result look like?', 'How will they know it went well? e.g. "The customer has the application link and a date to apply by."', 2],
  ['avoid', 'What should they avoid?', 'Common mistakes. e.g. "Never promise discounts. Never call before 9am."', 2],
  ['ask_help', 'When should they stop and ask?', 'Situations they must not handle alone. e.g. "If the customer complains or asks for a refund."', 2],
]
const TIPS = {
  task: 'Pick one task at a time, something you could explain in a couple of minutes. You can teach more tasks later.',
  teach: 'Explain it the way you would to a new hire. Say when it happens, what to do first, then what, and what a good result looks like. Mention the tools: phone, email, spreadsheets, documents.',
  review: 'Read the steps like a stranger would. Could someone follow them without asking you anything? Add what is missing, remove what is not needed.',
  practice: 'Give them a made-up case. Nothing real happens in practice: no calls, no emails. Watch what they do. If it is wrong, say what you would change and they update the steps.',
  approve: 'Once you approve, the steps go into their playbook and they use them from now on. You can come back and change them any time.',
}
const SCEN = ['A typical case where everything goes smoothly', 'A tricky case: the customer says they are not interested', 'A case with missing information (no phone number or email)']

export default function Train({ a }) {
  const [list, setList] = useState([])
  const [t, setT] = useState(null)
  const [step, setStep] = useState('task')
  const [form, setForm] = useState({ name: '', goal: '' })
  const [mode, setMode] = useState('')
  const [rough, setRough] = useState('')
  const [reply, setReply] = useState('')
  const [scen, setScen] = useState('')
  const [run, setRun] = useState(null)
  const [fb, setFb] = useState('')
  const [rating, setRating] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const loadList = () => api.get(`/agents/${a.id}/trainings`).then(setList).catch(() => {})
  useEffect(() => { loadList(); setT(null) }, [a.id]) // eslint-disable-line
  const guard = async (name, fn) => { setMsg(''); setBusy(name); try { await fn() } catch (e) { setMsg(e.message) } setBusy('') }
  const open = async (id) => { const x = await api.get(`/trainings/${id}`); setT(x); setStep(x.status === 'approved' ? 'approve' : x.steps ? 'review' : 'teach'); setMode(''); setRun(null) }
  const fieldChange = (k, v) => setT({ ...t, [k]: v })
  const saveFields = async () => { const x = await api.put(`/trainings/${t.id}`, Object.fromEntries(FIELDS.map(([k]) => [k, t[k]]))); setT(x) }

  const start = () => guard('start', async () => { const x = await api.post(`/agents/${a.id}/trainings`, form); setT(x); setStep('teach'); setForm({ name: '', goal: '' }); loadList() })
  const interview = (message) => guard('chat', async () => {
    const r = await api.post(`/trainings/${t.id}/interview`, { message }); setT(r.training); setReply('')
    if (r.done) setMsg('')
  })
  const structure = () => guard('structure', async () => { const x = await api.post(`/trainings/${t.id}/structure`, { rough }); setT(x); setStep('review') })
  const practice = () => guard('practice', async () => { await saveFields(); const r = await api.post(`/trainings/${t.id}/practice`, { scenario: scen }); setRun(r); setRating(''); setFb(''); setT(await api.get(`/trainings/${t.id}`)) })
  const sendFeedback = (r) => guard('fb', async () => {
    const x = await api.post(`/trainings/${t.id}/feedback`, { run_id: run.run_id, rating: r, feedback: fb }); setT(x); setRating(r)
    if (r === 'good') setStep('approve'); else setMsg(`Updated the steps (version ${x.version}). Run the practice again to check.`)
    setRun(null)
  })
  const approve = () => guard('approve', async () => { const x = await api.post(`/trainings/${t.id}/approve`, { repeat }); setT(x); loadList(); setMsg(`${a.name} has learned "${x.name}".`) })

  if (!t) {
    return (
      <div className="two-col">
        <section className="panel"><div className="panel-b form-grid">
          <h3>Teach {a.name} a task</h3>
          <p className="muted small">{TIPS.task}</p>
          <label className="f">What is the task called?<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Chase a warm lead" /></label>
          <label className="f">What should it achieve? (optional)<input value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} placeholder="e.g. They apply within a week" /></label>
          <div className="actions"><button className="btn solid" disabled={!form.name.trim() || busy} onClick={start}>Start teaching</button></div>
          {msg && <p className="warn small">{msg}</p>}
        </div></section>
        <section className="panel"><div className="panel-b">
          <h3>Already taught</h3>
          {list.length === 0 && <p className="muted small">Nothing yet.</p>}
          <ul className="plain">{list.map((x) => <li key={x.id}><button className="link" onClick={() => open(x.id)}>{x.name}</button> <span className={`tag ${x.status === 'approved' ? 'done' : ''}`}>{x.status === 'approved' ? `learned · v${x.version}` : x.status === 'practicing' ? 'practising' : 'draft'}</span></li>)}</ul>
        </div></section>
      </div>
    )
  }

  const idx = STEPS.findIndex(([k]) => k === step)
  return (
    <div>
      <div className="actions" style={{ justifyContent: 'space-between' }}>
        <button className="link" onClick={() => { setT(null); loadList() }}>← All training</button>
        <b>{t.name} <span className="muted small">version {t.version}</span></b>
      </div>
      <ol className="stepper">{STEPS.map(([k, n], i) => <li key={k} className={`${i === idx ? 'on' : ''} ${i < idx ? 'past' : ''}`}><button onClick={() => setStep(k)}><i>{i < idx ? '✓' : i + 1}</i> {n}</button></li>)}</ol>
      <div className="tip"><b>Tip.</b> {TIPS[step]}</div>
      {msg && <div className="notice" onClick={() => setMsg('')}>{msg}</div>}

      {step === 'task' && (
        <section className="panel"><div className="panel-b form-grid">
          <label className="f">Task name<input value={t.name} onChange={(e) => fieldChange('name', e.target.value)} onBlur={saveFields} /></label>
          <label className="f">Goal<input value={t.goal} onChange={(e) => fieldChange('goal', e.target.value)} onBlur={() => api.put(`/trainings/${t.id}`, { goal: t.goal })} /></label>
          <div className="actions"><button className="btn solid" onClick={() => setStep('teach')}>Next: teach it</button></div>
        </div></section>
      )}

      {step === 'teach' && (
        <div className="two-col">
          <section className={`panel choice ${mode === 'interview' ? 'on' : ''}`}><div className="panel-b form-grid">
            <h3>Let {a.name} interview me</h3>
            <p className="muted small">{a.name} asks one question at a time and writes the steps for you. Easiest if you are not sure where to start.</p>
            {mode !== 'interview' && <div className="actions"><button className="btn solid" disabled={!!busy} onClick={() => { setMode('interview'); if (!t.chat.length) interview('') }}>{t.chat.length ? 'Continue the interview' : 'Start the interview'}</button></div>}
            {mode === 'interview' && (
              <>
                <div className="chat">{t.chat.map((m, i) => <div key={i} className={`bub ${m.role}`}><small>{m.role === 'admin' ? 'You' : a.name}</small><p>{m.text}</p></div>)}{busy === 'chat' && <div className="bub agent"><p className="muted">…</p></div>}</div>
                <form className="actions" onSubmit={(e) => { e.preventDefault(); if (reply.trim()) interview(reply.trim()) }}>
                  <input style={{ flex: 1 }} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Your answer" />
                  <button className="btn solid" disabled={!reply.trim() || !!busy}>Send</button>
                </form>
                {t.steps && <div className="actions"><span className="ok small">Draft is ready.</span><button className="btn solid" onClick={() => setStep('review')}>Check the steps</button></div>}
              </>
            )}
          </div></section>
          <section className={`panel choice ${mode === 'tell' ? 'on' : ''}`}><div className="panel-b form-grid">
            <h3>I'll explain it myself</h3>
            <p className="muted small">Write or paste how you do it, as rough as you like. {a.name} tidies it into clear steps that you can edit.</p>
            <textarea rows="8" value={rough} onFocus={() => setMode('tell')} onChange={(e) => setRough(e.target.value)} placeholder={'e.g. Every morning look at who applied but never answered. Call them. If they pick up and are interested, text them the application link. If not, email them and try again in 2 days. Write the result in the call tracker sheet.'} />
            <div className="actions"><button className="btn solid" disabled={rough.trim().length < 10 || !!busy} onClick={structure}>{busy === 'structure' ? 'Writing the steps…' : 'Turn it into steps'}</button></div>
          </div></section>
        </div>
      )}

      {step === 'review' && (
        <section className="panel"><div className="panel-b form-grid">
          {!t.steps && <p className="warn small">No steps yet. Go back and teach it first.</p>}
          {FIELDS.map(([k, label, help, rows]) => (
            <label key={k} className="f"><b className="flabel">{label}</b><small>{help}</small><textarea rows={rows} value={t[k] || ''} onChange={(e) => fieldChange(k, e.target.value)} /></label>
          ))}
          <div className="actions"><button className="btn" onClick={() => setStep('teach')}>Back</button><button className="btn solid" disabled={!t.steps?.trim()} onClick={async () => { await saveFields(); setStep('practice') }}>Save and practise</button></div>
        </div></section>
      )}

      {step === 'practice' && (
        <div className="two-col">
          <section className="panel"><div className="panel-b form-grid">
            <h3>Run a practice</h3>
            <div className="chips">{SCEN.map((s) => <button key={s} className="chip" onClick={() => setScen(s)}>{s}</button>)}</div>
            <label className="f">Describe the case<textarea rows="3" value={scen} onChange={(e) => setScen(e.target.value)} placeholder="e.g. Sam Patel applied 5 days ago, never replied. Phone +44 7700 900111." /></label>
            <div className="actions"><button className="btn solid" disabled={scen.trim().length < 5 || !!busy} onClick={practice}>{busy === 'practice' ? `${a.name} is practising…` : 'Run practice'}</button></div>
            {run && (
              <div className="result-box">
                <h4>What {a.name} did</h4>
                <p>{run.output || 'No summary.'}</p>
                {run.trace.length > 0 && <><h4>Steps they took</h4><ol className="small">{run.trace.map((x, i) => <li key={i}><code>{x.tool}</code> <span className="muted">{Object.entries(x.args || {}).slice(0, 3).map(([k, v]) => `${k}: ${String(v).slice(0, 40)}`).join(', ')}</span></li>)}</ol></>}
                <h4>Is that how you would do it?</h4>
                <div className="actions"><button className="btn solid" disabled={!!busy} onClick={() => sendFeedback('good')}>Yes, that's right</button><button className={`btn ${rating === 'needs_work' ? 'on' : ''}`} onClick={() => setRating('needs_work')}>Needs changes</button></div>
                {rating === 'needs_work' && (
                  <div className="form-grid" style={{ marginTop: 8 }}>
                    <textarea rows="3" value={fb} onChange={(e) => setFb(e.target.value)} placeholder="What should they do differently? e.g. Check the do-not-call flag before calling." />
                    <div className="actions"><button className="btn solid" disabled={!fb.trim() || !!busy} onClick={() => sendFeedback('needs_work')}>{busy === 'fb' ? 'Updating…' : 'Update the steps'}</button></div>
                  </div>
                )}
              </div>
            )}
          </div></section>
          <section className="panel"><div className="panel-b">
            <h3>Current steps (v{t.version})</h3>
            <pre className="mailbody">{t.steps}</pre>
            <button className="link" onClick={() => setStep('review')}>Edit the steps</button>
            {t.runs.length > 0 && <><h4 style={{ marginTop: 12 }}>Earlier practice</h4><ul className="plain">{t.runs.map((r) => <li key={r.id} className="small"><span className={`tag ${r.rating === 'good' ? 'done' : ''}`}>{r.rating === 'good' ? 'good' : r.rating ? 'changed' : 'unrated'}</span> {r.scenario.slice(0, 80)}{r.feedback ? <span className="muted"> — {r.feedback.slice(0, 80)}</span> : null}</li>)}</ul></>}
            <div className="actions" style={{ marginTop: 12 }}><button className="btn solid" onClick={() => setStep('approve')}>I'm happy with it</button></div>
          </div></section>
        </div>
      )}

      {step === 'approve' && (
        <section className="panel"><div className="panel-b form-grid">
          <h3>{t.status === 'approved' ? `${a.name} knows this task` : `Teach ${a.name} this task`}</h3>
          <pre className="mailbody">{[t.when_to_use && `When: ${t.when_to_use}`, t.steps].filter(Boolean).join('\n\n')}</pre>
          <label className="f">Should {a.name} do this on a schedule?
            <select value={repeat} onChange={(e) => setRepeat(e.target.value)}><option value="">Only when asked or when it comes up</option><option value="daily">Every day, on their own</option><option value="weekly">Every week, on their own</option></select>
          </label>
          <div className="actions"><button className="btn" onClick={() => setStep('practice')}>Practise more</button><button className="btn solid" disabled={!!busy} onClick={approve}>{t.status === 'approved' ? 'Save changes' : `Approve and teach ${a.name}`}</button></div>
        </div></section>
      )}
    </div>
  )
}
