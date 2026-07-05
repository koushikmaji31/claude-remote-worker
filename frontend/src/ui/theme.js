import { useEffect, useState } from 'react'

const KEY = 'theme'

export function applyTheme(theme) {
  const root = document.documentElement
  if (theme === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', theme)
}

export function getStoredTheme() {
  return localStorage.getItem(KEY) || 'system'
}

/** Cycles system -> light -> dark -> system */
export function useTheme() {
  const [theme, setTheme] = useState(getStoredTheme)

  useEffect(() => {
    applyTheme(theme)
    if (theme === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, theme)
  }, [theme])

  return [theme, setTheme]
}
