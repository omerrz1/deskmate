import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import Icon from './Icon'

const Ctx = createContext(null)
export const useToast = () => useContext(Ctx).toast
export const useConfirm = () => useContext(Ctx).confirm
export const usePrompt = () => useContext(Ctx).prompt

/** Toasts, confirm dialogs and prompt dialogs, so nothing in the app uses the browser's own pop-ups. */
export function FeedbackProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [dlg, setDlg] = useState(null)
  const n = useRef(0)
  const push = useCallback((kind, text) => {
    const id = ++n.current
    setToasts((t) => [...t.slice(-3), { id, kind, text }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 6500 : 3500)
  }, [])
  const toast = useMemo(() => ({ ok: (t) => push('ok', t), err: (t) => push('error', t), info: (t) => push('info', t) }), [push])
  const confirm = useCallback((o) => new Promise((res) => setDlg({ kind: 'confirm', ...(typeof o === 'string' ? { body: o } : o), res })), [])
  const prompt = useCallback((o) => new Promise((res) => setDlg({ kind: 'prompt', value: '', ...(typeof o === 'string' ? { title: o } : o), res })), [])
  const value = useMemo(() => ({ toast, confirm, prompt }), [toast, confirm, prompt])
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role="status" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
            <Icon name={t.kind === 'error' ? 'alert' : t.kind === 'ok' ? 'check' : 'info'} size={16} /><span>{t.text}</span>
          </div>
        ))}
      </div>
      {dlg && <Dialog dlg={dlg} close={(v) => { dlg.res(v); setDlg(null) }} />}
    </Ctx.Provider>
  )
}

function Dialog({ dlg, close }) {
  const [v, setV] = useState(dlg.value || '')
  const ok = useRef(null)
  const prompt = dlg.kind === 'prompt'
  useEffect(() => {
    const k = (e) => { if (e.key === 'Escape') close(prompt ? null : false); if (e.key === 'Enter' && !e.shiftKey && !(e.target.tagName === 'TEXTAREA')) { e.preventDefault(); if (!prompt || v.trim()) close(prompt ? v.trim() : true) } }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  })
  useEffect(() => { if (!prompt) ok.current?.focus() }, [prompt])
  return (
    <div className="modal-bg" onMouseDown={() => close(prompt ? null : false)}>
      <div className="dialog" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <h3>{dlg.title || (prompt ? 'Enter a value' : 'Are you sure?')}</h3>
        {dlg.body && <p className="muted">{dlg.body}</p>}
        {prompt && <input autoFocus type={dlg.secret ? 'password' : 'text'} autoComplete={dlg.secret ? 'current-password' : 'off'} aria-label={dlg.label || dlg.title} value={v} placeholder={dlg.placeholder || ''} onChange={(e) => setV(e.target.value)} />}
        <div className="actions" style={{ justifyContent: 'flex-end', marginTop: 6 }}>
          <button className="btn" onClick={() => close(prompt ? null : false)}>{dlg.cancel || 'Cancel'}</button>
          <button ref={ok} className={`btn ${dlg.danger ? 'danger-solid' : 'solid'}`} disabled={prompt && !v.trim()} onClick={() => close(prompt ? v.trim() : true)}>{dlg.confirm || (prompt ? 'OK' : 'Confirm')}</button>
        </div>
      </div>
    </div>
  )
}
