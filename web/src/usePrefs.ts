// The persisted view prefs as React state (storage rules: settings.ts).

import { useCallback, useState } from 'react'
import { clampSize, readPrefs, savePref, type Prefs } from './settings'
import { applyAppearance } from './theme'

export function usePrefs() {
  const [prefs, setPrefs] = useState<Prefs>(() => readPrefs())
  const set = useCallback(<K extends keyof Prefs>(key: K, raw: Prefs[K]) => {
    const value = (key === 'tileSize' ? clampSize(raw as number) : raw) as Prefs[K]
    setPrefs((p) => {
      if (p[key] === value) return p
      const next = { ...p, [key]: value }
      if (key === 'theme' || key === 'density') applyAppearance(next.theme, next.density)
      return next
    })
    savePref(key, value)
  }, [])
  const setPanelOpen = useCallback((v: boolean) => set('panelOpen', v), [set])
  const setGridLayout = useCallback((v: Prefs['gridLayout']) => set('gridLayout', v), [set])
  const setTileSize = useCallback((v: number) => set('tileSize', v), [set])
  const setMobileCols = useCallback((v: Prefs['mobileCols']) => set('mobileCols', v), [set])
  const setTheme = useCallback((v: Prefs['theme']) => set('theme', v), [set])
  const setDensity = useCallback((v: Prefs['density']) => set('density', v), [set])
  return { ...prefs, setPanelOpen, setGridLayout, setTileSize, setMobileCols, setTheme, setDensity }
}
