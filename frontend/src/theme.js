import { useEffect, useState } from 'react'

const KEY = 'deskmates-theme'  // 'light' | 'dark' | 'system'
export const getTheme = () => { try { return localStorage.getItem(KEY) || 'system' } catch { return 'system' } }
export function applyTheme(t) {
  const dark = t === 'dark' || (t === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

/** The theme choice (saved on this device) and a setter. "system" follows the computer's own setting. */
export function useTheme() {
  const [t, setT] = useState(getTheme)
  useEffect(() => {
    applyTheme(t)
    try { localStorage.setItem(KEY, t) } catch { /* private mode */ }
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const f = () => { if (t === 'system') applyTheme('system') }
    mq.addEventListener('change', f)
    return () => mq.removeEventListener('change', f)
  }, [t])
  return [t, setT]
}
