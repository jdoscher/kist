import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, emptyQuery, type Item, type Query } from './api'
import { folderSubtree, mergeQuery, stillMatches } from './query'
import { fromUrl, historyAction, layerOf, toUrl, type Place } from './url'
import { runEach, serialize } from './writes'
import {
  TILE_SIZE_DEFAULT,
  TILE_SIZE_MAX,
  TILE_SIZE_MIN,
  type GridLayout,
} from './layout'
import type { MobileCols } from './mobile'

interface AppState {
  query: Query
  setQuery: (patch: Partial<Query>) => void
  resetQuery: () => void
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

const PANEL_KEY = 'kist.panelOpen'
const LAYOUT_KEY = 'kist.gridLayout'
const SIZE_KEY = 'kist.tileSize'
const COLS_KEY = 'kist.mobileCols'

function loadSetting(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function saveSetting(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* private mode */
  }
}

function clampSize(n: number): number {
  if (!Number.isFinite(n)) return TILE_SIZE_DEFAULT
  return Math.min(TILE_SIZE_MAX, Math.max(TILE_SIZE_MIN, Math.round(n)))
}

function loadLayout(): GridLayout {
  return loadSetting(LAYOUT_KEY) === 'square' ? 'square' : 'justified'
}

function loadTileSize(): number {
  const raw = loadSetting(SIZE_KEY)
  return raw === null ? TILE_SIZE_DEFAULT : clampSize(Number(raw))
}

/** Older smart folders stored Unfiled as "no folder + untagged". */
function normalizeSaved(q: Partial<Query>): Query {
  const merged: Query = { ...emptyQuery, ...q, deleted: false }
  if (merged.folder === 'none' && merged.untagged && !merged.view) {
    return { ...merged, view: 'unfiled', folder: null, untagged: false }
  }
  return merged
}

function loadPanelOpen(): boolean {
  try {
    return localStorage.getItem(PANEL_KEY) !== '0'
  } catch {
    return true
  }
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
  const anchor = useRef<string | null>(null)
  const [panelOpen, setPanelOpenRaw] = useState(loadPanelOpen)

  const setPanelOpen = useCallback((open: boolean) => {
    setPanelOpenRaw(open)
    try {
      localStorage.setItem(PANEL_KEY, open ? '1' : '0')
    } catch {
      /* private mode */
    }
  }, [])

  const [gridLayout, setGridLayoutRaw] = useState<GridLayout>(loadLayout)
  const [tileSize, setTileSizeRaw] = useState<number>(loadTileSize)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [mobileCols, setMobileColsRaw] = useState<MobileCols>(() =>
    loadSetting(COLS_KEY) === '2' ? 2 : 3,
  )
  const setMobileCols = useCallback((cols: MobileCols) => {
    setMobileColsRaw(cols)
    saveSetting(COLS_KEY, String(cols))
  }, [])

  const setGridLayout = useCallback((layout: GridLayout) => {
    setGridLayoutRaw(layout)
    saveSetting(LAYOUT_KEY, layout)
  }, [])

  const setTileSize = useCallback((size: number) => {
    const n = clampSize(size)
    setTileSizeRaw(n)
    saveSetting(SIZE_KEY, String(n))
  }, [])

  const setQuery = useCallback((patch: Partial<Query>) => {
    setQueryRaw((prev) => mergeQuery(prev, patch))
    setSmartId(null)
    setSelection(new Set())
    setPreviewId(null)
  }, [])

  const resetQuery = useCallback(() => {
    setQueryRaw(emptyQuery)
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

  // ---- URL sync (see url.ts for the routes and the Back rules) ----
  const place: Place = useMemo(
    () => ({
      query,
      smartId,
      item: previewId ?? (selection.size === 1 ? [...selection][0] : null),
      full: previewId !== null,
    }),
    [query, smartId, selection, previewId],
  )
  const url = toUrl(place)
  const shown = useRef<Place>(initial) // what the address bar shows now
  const afterBack = useRef<Place | null>(null)

  useEffect(() => {
    // normalize the address once (e.g. /unfiled -> /, unknown paths -> /all)
    window.history.replaceState(window.history.state, '', toUrl(initial))
  }, [initial])

  useEffect(() => {
    const cur = shown.current
    const hs = window.history.state ?? {}
    const action = historyAction(cur, place, {
      mobile,
      curPushed: !!hs.kistLayer,
      curIsMenu: !!hs.kistMenu,
    })
    if (action === 'none') return
    const layered = layerOf(place, mobile) !== 'view'
    if (action === 'push') window.history.pushState({ kistLayer: layered }, '', url)
    else if (action === 'replace')
      window.history.replaceState({ kistLayer: !!hs.kistLayer && !hs.kistMenu && layered }, '', url)
    else {
      afterBack.current = place
      window.history.back()
    }
    shown.current = place
  }, [url, mobile]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onPop = () => {
      const hs = window.history.state ?? {}
      const target = afterBack.current
      afterBack.current = null
      setDrawerOpen(!!hs.kistMenu)
      if (target) {
        // we stepped back to close a layer; the app already shows `target`
        window.history.replaceState(
          { kistLayer: !!hs.kistLayer && layerOf(target, mobile) !== 'view' },
          '',
          toUrl(target),
        )
        shown.current = target
        return
      }
      const p = fromUrl(window.location.pathname, window.location.search)
      shown.current = p
      setQueryRaw(p.query)
      setSmartId(p.smartId)
      setSelection(new Set(p.item ? [p.item] : []))
      setPreviewId(p.full ? p.item : null)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [mobile])

  const openDrawer = useCallback(() => {
    setDrawerOpen(true)
    window.history.pushState({ kistMenu: true, kistLayer: true }, '', window.location.href)
  }, [])

  const closeDrawer = useCallback(() => {
    if (window.history.state?.kistMenu) window.history.back()
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

  const value = useMemo(
    () => ({
      query,
      setQuery,
      resetQuery,
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
    [query, smartId, applySmartFolder, selection, previewId, panelOpen, setPanelOpen, gridLayout, setGridLayout, tileSize, setTileSize, drawerOpen, openDrawer, closeDrawer, jobsOpen, mobile, mobileCols, setMobileCols, setQuery, resetQuery, selectOnly, toggleSelect, rangeSelect, clearSelection, selectAll],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAppState(): AppState {
  const state = useContext(Ctx)
  if (!state) throw new Error('useAppState outside provider')
  return state
}

const PENDING_POLL_MS = 3000

export function useItems(query: Query) {
  return useInfiniteQuery({
    queryKey: ['items', query],
    queryFn: ({ pageParam }) => api.listItems(query, pageParam || undefined),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // a refetch reloads every loaded page in sequence (measured: 37 pages,
    // 4.5 MB): edits patch the list in place instead (useApplyItems), and
    // coming back to the tab doesn't reload it
    refetchOnWindowFocus: false,
  })
}

type ItemsPages = { pages: { items: Item[]; next_cursor: string | null; total: number | null }[]; pageParams: unknown[] }

/**
 * Apply edits the API already answered with (it returns the updated item) to
 * every loaded list, without refetching: items that left a view are dropped
 * from it, and the lists are marked stale so the next visit reloads them.
 */
export function useApplyItems() {
  const qc = useQueryClient()
  return useCallback(
    (updated: Item[], removedIds: string[] = []) => {
      const byId = new Map(updated.map((i) => [i.id, i]))
      const removed = new Set(removedIds)
      const folders = qc.getQueryData<{ id: string; parent_id: string | null }[]>(['folders']) ?? []
      for (const [key, data] of qc.getQueriesData<ItemsPages>({ queryKey: ['items'] })) {
        if (!data) continue
        const q = key[1] as Query
        const sub = q.folder && q.folder !== 'none' ? folderSubtree(q.folder, folders) : undefined
        let dropped = 0
        const pages = data.pages.map((p) => {
          const items = p.items.flatMap((i) => {
            if (removed.has(i.id)) {
              dropped++
              return []
            }
            const next = byId.get(i.id)
            if (!next) return [i]
            if (!stillMatches(next, q, sub)) {
              dropped++
              return []
            }
            return [next]
          })
          return { ...p, items }
        })
        if (dropped && pages[0].total != null) pages[0] = { ...pages[0], total: pages[0].total - dropped }
        qc.setQueryData(key, { ...data, pages })
      }
      for (const i of updated) qc.setQueryData(['item', i.id], i)
      // stale, not refetched now: the next visit to a list reloads it
      qc.invalidateQueries({ queryKey: ['items'], refetchType: 'none' })
      // small lists whose counts may have changed
      qc.invalidateQueries({ queryKey: ['tags'] })
      qc.invalidateQueries({ queryKey: ['folders'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
    [qc],
  )
}

/** An item by id: from the loaded grid if it's there, else fetched (a link
 * can point at an item far down the list, or in another view). */
export function useItem(id: string | null, loaded: Item[]): Item | undefined {
  const found = id ? loaded.find((i) => i.id === id) : undefined
  const fetched = useQuery({
    queryKey: ['item', id],
    queryFn: () => api.getItem(id as string),
    enabled: !!id && !found,
    retry: false,
    refetchInterval: (q) => (q.state.data?.preview_status === 'pending' ? PENDING_POLL_MS : false),
  })
  if (!id) return undefined
  return found ?? fetched.data
}

export function useJobs(enabled: boolean) {
  return useQuery({
    queryKey: ['jobs', 'failed'],
    queryFn: () => api.listJobs('failed'),
    enabled,
  })
}

export function useFolders() {
  return useQuery({ queryKey: ['folders'], queryFn: api.listFolders })
}

export function useTags() {
  return useQuery({ queryKey: ['tags'], queryFn: api.listTags })
}

export function useSmartFolders() {
  return useQuery({ queryKey: ['smartFolders'], queryFn: api.listSmartFolders })
}

export function useStats() {
  return useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 30_000 })
}

/** Invalidate everything that shows item data. */
export function useInvalidateItems() {
  const qc = useQueryClient()
  return useCallback(() => {
    qc.invalidateQueries({ queryKey: ['items'] })
    qc.invalidateQueries({ queryKey: ['item'] })
    qc.invalidateQueries({ queryKey: ['jobs'] })
    qc.invalidateQueries({ queryKey: ['tags'] })
    qc.invalidateQueries({ queryKey: ['folders'] })
    qc.invalidateQueries({ queryKey: ['smartFolders'] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }, [qc])
}

export interface BulkInput {
  ids: string[]
  action:
    | { kind: 'move'; folderId: string | null } // replace memberships
    | { kind: 'addFolder'; folderId: string }
    | { kind: 'trash' }
    | { kind: 'restore' }
    | { kind: 'hardDelete' }
    | { kind: 'addTags'; tags: string[] }
    | { kind: 'removeTag'; tag: string }
    | { kind: 'rating'; rating: number }
}

export interface BulkResult {
  input: BulkInput
  ok: number
  failed: { id: string; error: string }[]
}

/** Bulk actions run item by item and carry on past failures; the result
 * says which failed and why, so the bar can offer Retry for just those. */
export function useBulkAction() {
  const apply = useApplyItems()
  return useMutation({
    mutationFn: async (input: BulkInput): Promise<BulkResult> => {
      const { ids, action } = input
      const removed: string[] = []
      const res = await runEach<Item | null>(ids, (id) =>
        serialize(id, async () => {
          switch (action.kind) {
            case 'move':
              return api.putFolders(id, action.folderId ? [action.folderId] : [])
            case 'addFolder':
              return api.patchFolders(id, { add: [action.folderId] })
            case 'trash':
              return (await api.trashItem(id)).item
            case 'restore':
              return api.restoreItem(id)
            case 'hardDelete':
              await api.hardDeleteItem(id)
              removed.push(id)
              return null
            case 'addTags':
              return api.patchTags(id, { add: action.tags })
            case 'removeTag':
              return api.patchTags(id, { remove: [action.tag] })
            case 'rating':
              return api.patchItem(id, { rating: action.rating })
          }
        }),
      )
      apply(res.done.filter((i): i is Item => i !== null), removed)
      return { input, ok: res.done.length, failed: res.failed }
    },
  })
}

export function flattenItems(pages: { items: Item[] }[] | undefined): Item[] {
  if (!pages) return []
  return pages.flatMap((p) => p.items)
}
