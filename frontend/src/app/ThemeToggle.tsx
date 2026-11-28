import { useLayoutEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'

type Theme = 'light' | 'dark'

export function updateThemeColor() {
  const background = getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim()
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', background)
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
  const isDark = theme === 'dark'
  const action = isDark ? 'Включить светлую тему' : 'Включить тёмную тему'

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
    updateThemeColor()
  }, [theme])

  function toggleTheme() {
    const next = isDark ? 'light' : 'dark'
    setTheme(next)
    const storageKey = document.documentElement.dataset.themeStorageKey
    if (!storageKey) return
    try {
      localStorage.setItem(storageKey, next)
    } catch {
      // The theme can still be changed for the current page.
    }
  }

  return <button type="button" className="theme-toggle" role="switch" aria-label="Тёмная тема" aria-checked={isDark}
    title={action} onClick={toggleTheme}>
    <Sun size={15} aria-hidden="true" />
    <Moon size={15} aria-hidden="true" />
  </button>
}
