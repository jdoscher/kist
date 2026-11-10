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
import { fromUrl, type Place, type Screen, type SettingsSection } from './url'
import { usePrefs } from './usePrefs'
import { inMenuLayer, pushMenuLayer, useUrlSync } from './urlSync'
import type { Density, ThemePref } from './theme'
import { heroFor, heroTransition } from './viewTransition'

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
  /** appearance prefs (theme.ts) */
  theme: ThemePref
  setTheme: (t: ThemePref) => void
  density: Density
  setDensity: (d: Density) => void
  /** gallery density tucks the sidebars away; this shows them for the session */
  chromeShown: boolean
  setChromeShown: (shown: boolean) => void
  /** the Settings screen (its own URL, /settings/<section>) */
  settings: SettingsSection | null
  openSettings: (section?: SettingsSection) => void
  closeSettings: () => void
  /** the Recent changes panel (batch undo) */
  recentOpen: boolean
  setRecentOpen: (open: boolean) => void
  /** a full screen over the library: boards, a board, triage (Milestone 3) */
  screen: Screen | null
  openScreen: (screen: Screen | null) => void
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
  const [previewId, setPreviewIdRaw] = useState<string | null>(initial.full ? initial.item : null)
  const previewRef = useRef(previewId)
  previewRef.current = previewId
  // opening and closing morph the tile into the preview (viewTransition.ts)
  const setPreviewId = useCallback((id: string | null) => {
    const prev = previewRef.current
    if (prev === id) return
    heroTransition(() => setPreviewIdRaw(id), heroFor(prev, id), prev === null)
  }, [])
  const [chromeShown, setChromeShown] = useState(false)
  const [settings, setSettings] = useState<SettingsSection | null>(initial.settings ?? null)
  // Settings was opened from inside the app, so Close can go Back to where
  // you were instead of adding a history entry
  const settingsFromApp = useRef(false)
  const [recentOpen, setRecentOpen] = useState(false)
  const [screen, setScreen] = useState<Screen | null>(initial.screen ?? null)
  const [jobsOpen, setJobsOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const anchor = useRef<string | null>(null)
  const prefs = usePrefs()

  const setQuery = useCallback((patch: Partial<Query>) => {
    setQueryRaw((prev) => mergeQuery(prev, patch))
    setSettings(null)
    setScreen(null)
    setSmartId(null)
    setSelection(new Set())
    setPreviewIdRaw(null)
  }, [])

  const applySmartFolder = useCallback((id: string, q: Partial<Query>) => {
    setQueryRaw(normalizeSaved(q))
    setSettings(null)
    setScreen(null)
    setSmartId(id)
    setSelection(new Set())
    setPreviewIdRaw(null)
  }, [])

  const place: Place = useMemo(
    () => ({
      query,
      smartId,
      item: settings || screen ? null : previewId ?? (selection.size === 1 ? [...selection][0] : null),
      full: !settings && !screen && previewId !== null,
      ...(settings ? { settings } : {}),
      ...(screen && !settings ? { screen } : {}),
    }),
    [query, smartId, selection, previewId, settings, screen],
  )
  useUrlSync({
    initial,
    place,
    mobile,
    onMenu: setDrawerOpen,
    onNavigate: (p) => {
      if (p.settings) {
        setSettings(p.settings)
        return
      }
      setSettings(null)
      setScreen(p.screen ?? null)
      if (p.screen) return
      settingsFromApp.current = false
      setQueryRaw(p.query)
      setSmartId(p.smartId)
      setSelection(new Set(p.item ? [p.item] : []))
      setPreviewIdRaw(p.full ? p.item : null)
    },
  })

  const openSettings = useCallback((section: SettingsSection = 'tokens') => {
    setSettings((cur) => {
      if (!cur) settingsFromApp.current = true
      return section
    })
    setDrawerOpen(false)
  }, [])

  const closeSettings = useCallback(() => {
    if (settingsFromApp.current) {
      settingsFromApp.current = false
      window.history.back() // popstate shows the library place again
    } else setSettings(null)
  }, [])

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

  const openScreen = useCallback((sc: Screen | null) => {
    setSettings(null)
    setScreen(sc)
    setPreviewIdRaw(null)
    setDrawerOpen(false)
  }, [])

  const clearSelection = useCallback(() => setSelection(new Set()), [])
  const selectAll = useCallback((ids: string[]) => setSelection(new Set(ids)), [])

  const {
    panelOpen,
    setPanelOpen,
    gridLayout,
    setGridLayout,
    tileSize,
    setTileSize,
    mobileCols,
    setMobileCols,
    theme,
    setTheme,
    density,
    setDensity,
  } = prefs
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
      theme,
      setTheme,
      density,
      setDensity,
      chromeShown,
      setChromeShown,
      settings,
      openSettings,
      closeSettings,
      recentOpen,
      setRecentOpen,
      screen,
      openScreen,
    }),
    [screen, openScreen, settings, openSettings, closeSettings, recentOpen, query, setQuery, smartId, applySmartFolder, selection, selectOnly, toggleSelect, rangeSelect, clearSelection, selectAll, previewId, setPreviewId, panelOpen, setPanelOpen, gridLayout, setGridLayout, tileSize, setTileSize, drawerOpen, openDrawer, closeDrawer, jobsOpen, mobile, mobileCols, setMobileCols, theme, setTheme, density, setDensity, chromeShown],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAppState(): AppState {
  const state = useContext(Ctx)
  if (!state) throw new Error('useAppState outside provider')
  return state
}
