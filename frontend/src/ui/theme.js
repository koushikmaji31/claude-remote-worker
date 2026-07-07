import { useEffect, useState } from 'react'

const KEY = 'theme'
const DEFAULT = 'dark'

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark')
}

export function getStoredTheme() {
  const t = localStorage.getItem(KEY)
  return t === 'light' || t === 'dark' ? t : DEFAULT
}

/** Two-state theme: dark <-> light (no system option). */
export function useTheme() {
  const [theme, setTheme] = useState(getStoredTheme)

  useEffect(() => {
    applyTheme(theme)
    localStorage.setItem(KEY, theme)
  }, [theme])

  return [theme, setTheme]
}
