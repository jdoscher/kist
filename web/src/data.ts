// Server data hooks: the queries, the write rule's cache side
// (useApplyItems, logic in cache.ts) and the narrow refreshes for the few
// writes whose effect on item lists can't be patched in place.

import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { api, emptyQuery, type GridItem, type Item, type Query } from './api'
import { flattenUnique, listIsNewer, needsRefetch, patchPages, type ItemsPages } from './cache'
import { useNotices } from './components/Notices'
import { isUnfiledView, stillMatches } from './query'
import { markOnce, onceSeen } from './settings'
import { expandStacks, getExpanded, subscribeExpanded } from './stacks'
import { useAppState } from './state'
import { capsOf, type Caps } from './tokens'

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

export function flattenItems(pages: { items: GridItem[] }[] | undefined): GridItem[] {
  return flattenUnique(pages)
}

/** The grid's current list (the app's query), flattened, with any stacks
 * opened in place (stacks.ts). */
export function useCurrentItems() {
  const { query } = useAppState()
  const result = useItems(query)
  const expanded = useSyncExternalStore(subscribeExpanded, getExpanded)
  const items = useMemo(() => expandStacks(flattenItems(result.data?.pages), expanded), [result.data, expanded])
  return { result, items }
}

/**
 * Apply edits the API already answered with (it returns the updated item) to
 * every loaded list, without refetching: items that left a view are dropped
 * from it, and the lists are marked stale so the next visit reloads them.
 */
export function useApplyItems() {
  const qc = useQueryClient()
  const filedHint = useFiledHint()
  return useCallback(
    (updated: Item[], removedIds: string[] = []) => {
      const byId = new Map(updated.map((i) => [i.id, i]))
      const removed = new Set(removedIds)
      const folders = qc.getQueryData<{ id: string; parent_id: string | null }[]>(['folders']) ?? []
      const moved: (readonly unknown[])[] = []
      const shownBefore = filedHint.snapshot()
      for (const [key, data] of qc.getQueriesData<ItemsPages>({ queryKey: ['items'] })) {
        if (!data) continue
        const query = key[1] as Query
        if (needsRefetch(data, query, byId, folders)) moved.push(key)
        const next = patchPages(data, query, byId, removed, folders)
        if (next !== data) qc.setQueryData(key, next)
      }
      for (const i of updated) qc.setQueryData(['item', i.id], i)
      // stale, not refetched now: the next visit to a list reloads it
      qc.invalidateQueries({ queryKey: ['items'], refetchType: 'none' })
      // ...except a list sorted by what changed: the item's place in it is
      // the server's call, so that one list reloads (if it's on screen)
      for (const key of moved) void qc.invalidateQueries({ queryKey: key, exact: true })
      filedHint.check(updated, shownBefore)
      // every edit is a history event and part of a batch
      qc.invalidateQueries({ queryKey: ['history'] })
      qc.invalidateQueries({ queryKey: ['batches'] })
      // small lists whose counts may have changed
      qc.invalidateQueries({ queryKey: ['tags'] })
      qc.invalidateQueries({ queryKey: ['folders'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
    [qc, filedHint],
  )
}

/** Unfiled means "no folder and no tag", so tagging an item there makes it
 * leave the list. The first time that happens, say where it went. */
function useFiledHint() {
  const { query, setQuery, selectOnly } = useAppState()
  const { notify } = useNotices()
  const qc = useQueryClient()
  const ref = useRef({ query, setQuery, selectOnly })
  ref.current = { query, setQuery, selectOnly }
  /** ids on screen in Unfiled before the edit (null: not in Unfiled) */
  const snapshot = useCallback((): Set<string> | null => {
    const { query: q } = ref.current
    if (!isUnfiledView(q) || onceSeen('filedHint')) return null
    return new Set(flattenUnique(qc.getQueryData<ItemsPages>(['items', q])?.pages).map((i) => i.id))
  }, [qc])
  const check = useCallback(
    (updated: Item[], shown: Set<string> | null) => {
      const { query: q } = ref.current
      if (!shown || !isUnfiledView(q)) return
      const left = updated.filter((i) => shown.has(i.id) && i.state === 'active' && !stillMatches(i, q))
      if (!left.length) return
      markOnce('filedHint')
      const first = left[0]
      notify({
        key: 'filed',
        message:
          left.length === 1
            ? `Filed: “${first.filename}” moved out of Unfiled, which lists only items with no folder or tag.`
            : `Filed: ${left.length} items moved out of Unfiled, which lists only items with no folder or tag.`,
        action: {
          label: 'See it in All items',
          run: () => {
            ref.current.setQuery({ ...emptyQuery, order: q.order })
            ref.current.selectOnly(first.id)
          },
        },
        timeoutMs: 12_000,
      })
    },
    [notify],
  )
  return useMemo(() => ({ snapshot, check }), [snapshot, check])
}

/**
 * Narrow refreshes, one per kind of change, instead of reloading every
 * list. Item edits never need these: they go through useApplyItems.
 */
export function useRefresh() {
  const qc = useQueryClient()
  return useMemo(
    () => ({
      /** folder created or renamed: only the folder list changed */
      folders: () => void qc.invalidateQueries({ queryKey: ['folders'] }),
      smartFolders: () => void qc.invalidateQueries({ queryKey: ['smartFolders'] }),
      /** an import job was retried or dismissed */
      jobs: () => {
        void qc.invalidateQueries({ queryKey: ['jobs'] })
        void qc.invalidateQueries({ queryKey: ['stats'] })
      },
      /** folder deleted: the server dropped memberships we can't see, so the
       * list on screen reloads and every other list is marked stale */
      folderDeleted: () => {
        void qc.invalidateQueries({ queryKey: ['folders'] })
        void qc.invalidateQueries({ queryKey: ['stats'] })
        void qc.invalidateQueries({ queryKey: ['item'] })
        void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
      },
      /** the list on screen reloads (others are marked stale): for items
       * that came back from the trash, whose counts useApplyItems already
       * refreshed */
      items: () => void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' }),
      /** items were added or came back from the trash: where they sort in a
       * list is the server's call, so the list on screen reloads */
      itemsAdded: () => {
        void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
        void qc.invalidateQueries({ queryKey: ['tags'] })
        void qc.invalidateQueries({ queryKey: ['folders'] })
        void qc.invalidateQueries({ queryKey: ['stats'] })
      },
    }),
    [qc],
  )
}

/** An item by id: from the loaded grid if it's there, else fetched (a link
 * can point at an item far down the list, or in another view). While its
 * preview is being made it is re-checked every few seconds (the one poll for
 * pending previews), and the answer is applied to every list. */
/**
 * The full item for an id (GET /api/items/{id}, cached as ['item', id]).
 * Lists hold slim items (GridItem, fields=grid; P4), so the info panel, the
 * phone sheet and the full-screen preview get the whole item here; it is
 * undefined until it has arrived. Edits keep the cache current
 * (useApplyItems writes every answer into ['item', id]); when the list
 * knows a newer version than the cached copy (an edit from elsewhere, a
 * preview that finished) it is fetched again. While its preview is pending
 * it is polled, and the lists get the finished item.
 */
export function useItem(id: string | null, loaded: GridItem[]): Item | undefined {
  const found = id ? loaded.find((i) => i.id === id) : undefined
  const apply = useApplyItems()
  const fetched = useQuery({
    queryKey: ['item', id],
    queryFn: () => api.getItem(id as string),
    enabled: !!id,
    retry: false,
    // selecting the same item again within a minute uses the copy edits kept current
    staleTime: 60_000,
    refetchInterval: (q) =>
      (q.state.data ?? found)?.preview_status === 'pending' ? PENDING_POLL_MS : false,
  })
  const fresh = fetched.data && fetched.data.id === id ? fetched.data : undefined
  const behind = !!found && !!fresh && listIsNewer(found, fresh)
  const { refetch, isFetching } = fetched
  useEffect(() => {
    if (behind && !isFetching) void refetch()
  }, [behind]) // eslint-disable-line react-hooks/exhaustive-deps
  const settled = !!found && found.preview_status === 'pending' && !!fresh && fresh.preview_status !== 'pending'
  useEffect(() => {
    if (settled && fresh) apply([fresh])
  }, [settled, fresh, apply])
  if (!id) return undefined
  return fresh
}

/**
 * useItem for a view that stays open while its id changes (info panel,
 * phone sheet, full-screen preview): until the new item's full copy has
 * arrived (a few ms; usually already cached) the previous one stays, so the
 * view doesn't blink out and back in.
 */
export function useShownItem(id: string | null, loaded: GridItem[]): Item | undefined {
  const full = useItem(id, loaded)
  const last = useRef<Item | undefined>(undefined)
  if (!id) last.current = undefined
  else if (full) last.current = full
  return id ? (full ?? last.current) : undefined
}

/** Fetch these items' full copies ahead (the preview's neighbours), so
 * stepping to them shows at once. */
export function usePrefetchItems(ids: (string | undefined)[]) {
  const qc = useQueryClient()
  const key = ids.filter(Boolean).join(',')
  useEffect(() => {
    for (const id of key ? key.split(',') : [])
      void qc.prefetchQuery({ queryKey: ['item', id], queryFn: () => api.getItem(id), staleTime: 60_000, retry: false })
  }, [qc, key])
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

/** Folder id -> name, for chips. */
export function useFolderNames(): Map<string, string> {
  const { data } = useFolders()
  return useMemo(() => new Map((data ?? []).map((f) => [f.id, f.name])), [data])
}

// a folder-limited read token gets 404 for tags, smart folders and stats
// (they'd tell it about the rest of the library): don't ask
export function useTags() {
  const { restricted, ready } = useCaps()
  return useQuery({ queryKey: ['tags'], queryFn: api.listTags, enabled: ready && !restricted })
}

export function useSmartFolders() {
  const { restricted, ready } = useCaps()
  return useQuery({ queryKey: ['smartFolders'], queryFn: api.listSmartFolders, enabled: ready && !restricted })
}

export function useStats() {
  const { restricted, ready } = useCaps()
  return useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 30_000, enabled: ready && !restricted })
}

/** Who the signed-in token is (GET /api/me). */
export function useMe() {
  return useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 5 * 60_000, retry: false })
}

/** What the signed-in token may do: edit, delete for good, manage tokens,
 * and whether it's a folder-limited reader (tokens.ts). */
export function useCaps(): Caps & { ready: boolean } {
  const { data, isPending } = useMe()
  // an older server without /api/me (error) is the owner's
  return useMemo(() => ({ ...capsOf(data), ready: !isPending }), [data, isPending])
}

/** Recent batches of changes (GET /api/batches), for Recent changes. */
export function useBatches(enabled: boolean) {
  return useQuery({ queryKey: ['batches'], queryFn: () => api.batches(30), enabled, refetchInterval: enabled ? 15_000 : false })
}

/** After an undo or revert: the server changed items we can't patch in
 * place (the answer has no items), so the list on screen reloads and the
 * rest are marked stale. */
export function useAfterUndo() {
  const qc = useQueryClient()
  return useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
    void qc.invalidateQueries({ queryKey: ['item'] })
    void qc.invalidateQueries({ queryKey: ['history'] })
    void qc.invalidateQueries({ queryKey: ['links'] })
    void qc.invalidateQueries({ queryKey: ['batches'] })
    void qc.invalidateQueries({ queryKey: ['tags'] })
    void qc.invalidateQueries({ queryKey: ['folders'] })
    void qc.invalidateQueries({ queryKey: ['stats'] })
  }, [qc])
}


/** Server version and commit (GET /api/version), shown in the sidebar
 * footer and the library summary. */
export function useVersion() {
  return useQuery({ queryKey: ['version'], queryFn: api.version, staleTime: Infinity, retry: false })
}
