// Milestone 3 app-wide pieces that open from anywhere: the ⌘K palette,
// Compare/Survey, bulk rename, apply template and quick look. One context,
// so the grid, the bulk bar, the palette and the info panel can open them
// without each owning state (and without touching state.tsx).

import { createContext, lazy, Suspense, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { useAppState } from '../../state'
import { useShortcut } from '../../useShortcuts'
import { QuickLook } from './QuickLook'

const CommandPalette = lazy(() => import('./CommandPalette'))
const Compare = lazy(() => import('./Compare'))
const RenameDialog = lazy(() => import('./RenameDialog'))
const ApplyTemplateDialog = lazy(() => import('./Templates').then((m) => ({ default: m.ApplyTemplateDialog })))

export type CompareMode = 'compare' | 'survey'

interface CreativeApi {
  openPalette: () => void
  /** Compare (2-4 items) or Survey (2+) */
  openCompare: (ids: string[], mode?: CompareMode) => void
  openRename: (ids: string[]) => void
  openTemplate: (ids: string[]) => void
}

const Ctx = createContext<CreativeApi | null>(null)

export function useCreative(): CreativeApi {
  const c = useContext(Ctx)
  if (!c) throw new Error('useCreative outside CreativeProvider')
  return c
}

export const COMPARE_MAX = 4
export const SURVEY_MAX = 12

export function CreativeProvider({ children }: { children: ReactNode }) {
  const { selection, screen, settings } = useAppState()
  const [palette, setPalette] = useState(false)
  const [compare, setCompare] = useState<{ ids: string[]; mode: CompareMode } | null>(null)
  const [rename, setRename] = useState<string[] | null>(null)
  const [template, setTemplate] = useState<string[] | null>(null)

  const openCompare = useCallback((ids: string[], mode: CompareMode = 'compare') => {
    if (ids.length < 2) return
    setCompare({ ids: ids.slice(0, mode === 'compare' ? COMPARE_MAX : SURVEY_MAX), mode })
  }, [])
  const api = useMemo<CreativeApi>(
    () => ({
      openPalette: () => setPalette(true),
      openCompare,
      openRename: (ids) => ids.length && setRename(ids),
      openTemplate: (ids) => ids.length && setTemplate(ids),
    }),
    [openCompare],
  )

  // ⌘K / Ctrl+K from the library, the preview, a board or triage
  const paletteKey = (e: { key: string; metaKey: boolean; ctrlKey: boolean; preventDefault(): void }) => {
    if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'k') return false
    e.preventDefault()
    setPalette(true)
    return true
  }
  useShortcut('grid', paletteKey)
  useShortcut('preview', paletteKey)

  // C compares the selection, S surveys it (the library grid only)
  useShortcut(
    'grid',
    (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || screen || settings) return false
      const k = e.key.toLowerCase()
      if (k !== 'c' && k !== 's') return false
      const t = e.target as HTMLElement | null
      if (t && t !== document.body && !t.closest?.('.grid-scroll')) return false
      if (selection.size < 2) return false
      e.preventDefault()
      openCompare([...selection], k === 'c' ? 'compare' : 'survey')
      return true
    },
    selection.size >= 2,
  )

  return (
    <Ctx.Provider value={api}>
      {children}
      <QuickLook />
      <Suspense fallback={null}>
        {palette && <CommandPalette onClose={() => setPalette(false)} />}
        {compare && <Compare ids={compare.ids} mode={compare.mode} onMode={(mode) => setCompare({ ...compare, mode })} onClose={() => setCompare(null)} />}
        {rename && <RenameDialog ids={rename} onClose={() => setRename(null)} />}
        {template && <ApplyTemplateDialog ids={template} onClose={() => setTemplate(null)} />}
      </Suspense>
    </Ctx.Provider>
  )
}
