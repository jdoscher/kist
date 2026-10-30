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
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, emptyQuery, type Item, type Query } from './api'

interface AppState {
  query: Query
  setQuery: (patch: Partial<Query>) => void
  resetQuery: () => void
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
}

const PANEL_KEY = 'kist.panelOpen'

function loadPanelOpen(): boolean {
  try {
    return localStorage.getItem(PANEL_KEY) !== '0'
  } catch {
    return true
  }
}

const Ctx = createContext<AppState | null>(null)

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [query, setQueryRaw] = useState<Query>(emptyQuery)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [previewId, setPreviewId] = useState<string | null>(null)
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

  const setQuery = useCallback((patch: Partial<Query>) => {
    setQueryRaw((prev) => ({ ...prev, ...patch }))
    setSelection(new Set())
    setPreviewId(null)
  }, [])

  const resetQuery = useCallback(() => {
    setQueryRaw(emptyQuery)
    setSelection(new Set())
    setPreviewId(null)
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
    }),
    [query, selection, previewId, panelOpen, setPanelOpen, setQuery, resetQuery, selectOnly, toggleSelect, rangeSelect, clearSelection, selectAll],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAppState(): AppState {
  const state = useContext(Ctx)
  if (!state) throw new Error('useAppState outside provider')
  return state
}

export function useItems(query: Query) {
  return useInfiniteQuery({
    queryKey: ['items', query],
    queryFn: ({ pageParam }) => api.listItems(query, pageParam || undefined),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
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
    | { kind: 'addFolder'; folderId: string; itemFolders: Map<string, string[]> }
    | { kind: 'trash' }
    | { kind: 'restore' }
    | { kind: 'hardDelete' }
    | { kind: 'addTags'; tags: string[]; itemTags: Map<string, string[]> }
    | { kind: 'removeTag'; tag: string; itemTags: Map<string, string[]> }
    | { kind: 'rating'; rating: number }
}

export function useBulkAction() {
  const invalidate = useInvalidateItems()
  return useMutation({
    mutationFn: async ({ ids, action }: BulkInput) => {
      for (const id of ids) {
        switch (action.kind) {
          case 'move':
            await api.putFolders(id, action.folderId ? [action.folderId] : [])
            break
          case 'addFolder': {
            const current = action.itemFolders.get(id) ?? []
            if (!current.includes(action.folderId)) {
              await api.putFolders(id, [...current, action.folderId])
            }
            break
          }
          case 'trash':
            await api.trashItem(id)
            break
          case 'restore':
            await api.restoreItem(id)
            break
          case 'hardDelete':
            await api.hardDeleteItem(id)
            break
          case 'addTags': {
            const current = action.itemTags.get(id) ?? []
            const merged = [...new Set([...current, ...action.tags])]
            await api.putTags(id, merged)
            break
          }
          case 'removeTag': {
            const current = action.itemTags.get(id) ?? []
            await api.putTags(id, current.filter((t) => t !== action.tag))
            break
          }
          case 'rating':
            await api.patchItem(id, { rating: action.rating })
            break
        }
      }
    },
    onSettled: () => invalidate(),
  })
}

export function flattenItems(pages: { items: Item[] }[] | undefined): Item[] {
  if (!pages) return []
  return pages.flatMap((p) => p.items)
}
