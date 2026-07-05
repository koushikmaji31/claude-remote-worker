import { useTheme } from './theme.js'

const ICONS = { system: '🖥', light: '☀', dark: '🌙' }
const NEXT = { system: 'light', light: 'dark', dark: 'system' }
const LABEL = { system: 'System', light: 'Light', dark: 'Dark' }

export default function ThemeToggle() {
  const [theme, setTheme] = useTheme()
  return (
    <button
      className="btn ghost sm"
      title={`Theme: ${LABEL[theme]} (click to change)`}
      onClick={() => setTheme(NEXT[theme])}
    >
      <span aria-hidden>{ICONS[theme]}</span> {LABEL[theme]}
    </button>
  )
}
