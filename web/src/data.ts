// Server data hooks: the queries, the write rule's cache side
// (useApplyItems, logic in cache.ts) and the narrow refreshes for the few
// writes whose effect on item lists can't be patched in place.

import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo } from 'react'
import { api, type Item, type Query } from './api'
import { patchPages, type ItemsPages } from './cache'
import { useAppState } from './state'

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

export function flattenItems(pages: { items: Item[] }[] | undefined): Item[] {
  if (!pages) return []
  return pages.flatMap((p) => p.items)
}

/** The grid's current list (the app's query), flattened. */
export function useCurrentItems() {
  const { query } = useAppState()
  const result = useItems(query)
  const items = useMemo(() => flattenItems(result.data?.pages), [result.data])
  return { result, items }
}

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
        const next = patchPages(data, key[1] as Query, byId, removed, folders)
        if (next !== data) qc.setQueryData(key, next)
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
export function useItem(id: string | null, loaded: Item[]): Item | undefined {
  const found = id ? loaded.find((i) => i.id === id) : undefined
  const apply = useApplyItems()
  const fetched = useQuery({
    queryKey: ['item', id],
    queryFn: () => api.getItem(id as string),
    enabled: !!id && (!found || found.preview_status === 'pending'),
    retry: false,
    refetchInterval: (q) =>
      (q.state.data ?? found)?.preview_status === 'pending' ? PENDING_POLL_MS : false,
  })
  const fresh = fetched.data
  const settled = !!found && found.preview_status === 'pending' && !!fresh && fresh.id === found.id && fresh.preview_status !== 'pending'
  useEffect(() => {
    if (settled && fresh) apply([fresh])
  }, [settled, fresh, apply])
  if (!id) return undefined
  return found ?? fresh
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

export function useTags() {
  return useQuery({ queryKey: ['tags'], queryFn: api.listTags })
}

export function useSmartFolders() {
  return useQuery({ queryKey: ['smartFolders'], queryFn: api.listSmartFolders })
}

export function useStats() {
  return useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 30_000 })
}

