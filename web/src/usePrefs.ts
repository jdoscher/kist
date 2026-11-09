// The persisted view prefs as React state (storage rules: settings.ts).

import { useCallback, useState } from 'react'
import { clampSize, readPrefs, savePref, type Prefs } from './settings'

export function usePrefs() {
  const [prefs, setPrefs] = useState<Prefs>(() => readPrefs())
  const set = useCallback(<K extends keyof Prefs>(key: K, raw: Prefs[K]) => {
    const value = (key === 'tileSize' ? clampSize(raw as number) : raw) as Prefs[K]
    setPrefs((p) => (p[key] === value ? p : { ...p, [key]: value }))
    savePref(key, value)
  }, [])
  const setPanelOpen = useCallback((v: boolean) => set('panelOpen', v), [set])
  const setGridLayout = useCallback((v: Prefs['gridLayout']) => set('gridLayout', v), [set])
  const setTileSize = useCallback((v: number) => set('tileSize', v), [set])
  const setMobileCols = useCallback((v: Prefs['mobileCols']) => set('mobileCols', v), [set])
  return { ...prefs, setPanelOpen, setGridLayout, setTileSize, setMobileCols }
}
