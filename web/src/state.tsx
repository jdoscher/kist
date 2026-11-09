// The app's view state: what is shown (query, smart folder), what is
// selected, which layers are open, plus the persisted prefs. Server data
// lives in data.ts, bulk actions in bulk.ts, URL/history sync in urlSync.ts,
// pref storage in settings.ts.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import type { Query } from './api'
import type { GridLayout } from './layout'
import type { MobileCols } from './mobile'
import { mergeQuery, normalizeSaved } from './query'
import { fromUrl, type Place } from './url'
import { usePrefs } from './usePrefs'
import { inMenuLayer, pushMenuLayer, useUrlSync } from './urlSync'

interface AppState {
  query: Query
  setQuery: (patch: Partial<Query>) => void
  /** the smart folder being shown (cleared by any other filter change) */
  smartId: string | null
  applySmartFolder: (id: string, query: Partial<Query>) => void
  selection: Set<string>
  selectOnly: (id: string) => void
  toggleSelect: (id: string) => void
  rangeSelect: (id: string, flatIds: string[]) => void
  clearSelection: () => void
  selectAll: (ids: string[]) => void
  previewId: string | null
  setPreviewId: (id: string | null) => void
  panelOpen: boolean
  setPanelOpen: (open: boolean) => void
  gridLayout: GridLayout
  setGridLayout: (layout: GridLayout) => void
  tileSize: number
  setTileSize: (size: number) => void
  /** phone layout: the folder drawer and the grid's column count */
  drawerOpen: boolean
  setDrawerOpen: (open: boolean) => void
  /** open/close the phone menu as a history layer (Back closes it) */
  openDrawer: () => void
  closeDrawer: () => void
  /** the failed-imports panel */
  jobsOpen: boolean
  setJobsOpen: (open: boolean) => void
  mobile: boolean
  mobileCols: MobileCols
  setMobileCols: (cols: MobileCols) => void
}

const Ctx = createContext<AppState | null>(null)

export function AppStateProvider({ children, mobile }: { children: ReactNode; mobile: boolean }) {
  // the URL is where you are: read it once on load, then keep it in sync
  const [initial] = useState<Place>(() => fromUrl(window.location.pathname, window.location.search))
  const [query, setQueryRaw] = useState<Query>(initial.query)
  const [smartId, setSmartId] = useState<string | null>(initial.smartId)
  const [selection, setSelection] = useState<Set<string>>(
    () => new Set(initial.item ? [initial.item] : []),
  )
  const [previewId, setPreviewId] = useState<string | null>(initial.full ? initial.item : null)
  const [jobsOpen, setJobsOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const anchor = useRef<string | null>(null)
  const prefs = usePrefs()

  const setQuery = useCallback((patch: Partial<Query>) => {
    setQueryRaw((prev) => mergeQuery(prev, patch))
    setSmartId(null)
    setSelection(new Set())
    setPreviewId(null)
  }, [])

  const applySmartFolder = useCallback((id: string, q: Partial<Query>) => {
    setQueryRaw(normalizeSaved(q))
    setSmartId(id)
    setSelection(new Set())
    setPreviewId(null)
  }, [])

  const place: Place = useMemo(
    () => ({
      query,
      smartId,
      item: previewId ?? (selection.size === 1 ? [...selection][0] : null),
      full: previewId !== null,
    }),
    [query, smartId, selection, previewId],
  )
  useUrlSync({
    initial,
    place,
    mobile,
    onMenu: setDrawerOpen,
    onNavigate: (p) => {
      setQueryRaw(p.query)
      setSmartId(p.smartId)
      setSelection(new Set(p.item ? [p.item] : []))
      setPreviewId(p.full ? p.item : null)
    },
  })

  const openDrawer = useCallback(() => {
    setDrawerOpen(true)
    pushMenuLayer()
  }, [])

  const closeDrawer = useCallback(() => {
    if (inMenuLayer()) window.history.back()
    else setDrawerOpen(false)
  }, [])

  const selectOnly = useCallback((id: string) => {
    anchor.current = id
    setSelection(new Set([id]))
  }, [])

  const toggleSelect = useCallback((id: string) => {
    anchor.current = id
    setSelection((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const rangeSelect = useCallback((id: string, flatIds: string[]) => {
    const from = anchor.current ? flatIds.indexOf(anchor.current) : -1
    const to = flatIds.indexOf(id)
    if (from === -1 || to === -1) {
      anchor.current = id
      setSelection(new Set([id]))
      return
    }
    const [lo, hi] = from < to ? [from, to] : [to, from]
    setSelection((prev) => {
      const next = new Set(prev)
      for (let i = lo; i <= hi; i++) next.add(flatIds[i])
      return next
    })
  }, [])

  const clearSelection = useCallback(() => setSelection(new Set()), [])
  const selectAll = useCallback((ids: string[]) => setSelection(new Set(ids)), [])

  const { panelOpen, setPanelOpen, gridLayout, setGridLayout, tileSize, setTileSize, mobileCols, setMobileCols } =
    prefs
  const value = useMemo(
    () => ({
      query,
      setQuery,
      smartId,
      applySmartFolder,
      selection,
      selectOnly,
      toggleSelect,
      rangeSelect,
      clearSelection,
      selectAll,
      previewId,
      setPreviewId,
      panelOpen,
      setPanelOpen,
      gridLayout,
      setGridLayout,
      tileSize,
      setTileSize,
      drawerOpen,
      setDrawerOpen,
      openDrawer,
      closeDrawer,
      jobsOpen,
      setJobsOpen,
      mobile,
      mobileCols,
      setMobileCols,
    }),
    [query, setQuery, smartId, applySmartFolder, selection, selectOnly, toggleSelect, rangeSelect, clearSelection, selectAll, previewId, panelOpen, setPanelOpen, gridLayout, setGridLayout, tileSize, setTileSize, drawerOpen, openDrawer, closeDrawer, jobsOpen, mobile, mobileCols, setMobileCols],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAppState(): AppState {
  const state = useContext(Ctx)
  if (!state) throw new Error('useAppState outside provider')
  return state
}
