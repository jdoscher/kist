// The main grid's date scrubber data (M6 W1.1): the list's months from
// GET /api/items/buckets, the loaded window, and loading what the viewport
// needs (next page, the month before the window, or a jump to a month).
// The maths is in scrub.ts; the rail is components/Scrubber.tsx.

import { useQuery, useQueryClient, type InfiniteData, type UseInfiniteQueryResult } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { api, type Item, type ItemPage, type Query } from './api.ts'
import type { Row } from './layout.ts'
import {
  bucketAt,
  fitBucket,
  geometry,
  loadAction,
  offsetOfPosition,
  perItemHeight,
  positionAtOffset,
  scrubModel,
  scrubSort,
  untilOverlap,
  windowStart,
  type Geometry,
  type ScrubModel,
} from './scrub.ts'

/** What Grid restores after the next relayout (see Grid.tsx). */
export type Anchor =
  | { kind: 'item'; id: string; frac: number; pointY: number }
  /** a list position (a month's start after a jump) at the top */
  | { kind: 'pos'; pos: number }

export interface ScrubWindow {
  model: ScrubModel | null
  geom: Geometry | null
  /** estimated space above / below the loaded rows (virtualizer padding) */
  padStart: number
  padEnd: number
  /** list position at scroll offset y */
  positionAt: (y: number) => number
  /** scroll offset of list position pos */
  offsetOf: (pos: number) => number
  /** look at the viewport and load what it needs (debounce it yourself) */
  check: () => void
  /** go to list position pos: scroll there when it's loaded, else load the
   * window from its month's cursor and show that month's start */
  goTo: (pos: number) => void
}

type Pages = InfiniteData<ItemPage, string>

export function useScrubWindow(opts: {
  query: Query
  result: UseInfiniteQueryResult<InfiniteData<ItemPage, unknown>>
  items: Item[]
  rows: Row[]
  rowStartsLoaded: number[] // row tops from 0 (no padding)
  gap: number
  avail: number
  tileSize: number
  /** changes whenever the tile layout does (size, columns, mode) */
  layoutKey: string
  scrollEl: () => HTMLElement | null
  setAnchor: (a: Anchor) => void
}): ScrubWindow {
  const { query, result, items, rows, gap, avail, tileSize, layoutKey } = opts
  const qc = useQueryClient()
  const sort = scrubSort(query)
  const buckets = useQuery({
    queryKey: ['buckets', query],
    queryFn: () => api.itemBuckets(query)!,
    enabled: !!sort && !query.image,
    staleTime: 60_000,
    // an older server has no buckets endpoint: plain infinite scroll
    retry: false,
    refetchOnWindowFocus: false,
  })
  const model = useMemo(
    () => (sort && buckets.data && buckets.data.buckets.length >= 2 ? scrubModel(buckets.data) : null),
    [sort, buckets.data],
  )
  const firstCursor = result.data?.pageParams?.[0] as string | undefined
  // which month each cursor seen so far starts: a refetched bucket list may
  // carry new cursors, and the loaded window still starts at its month
  const cursorKeys = useRef(new Map<string, string | null>())
  if (model) for (const b of model.buckets) cursorKeys.current.set(b.cursor, b.key)
  let win = model ? windowStart(model, firstCursor) : 0
  if (model && firstCursor && win === 0 && model.buckets[0].cursor !== firstCursor) {
    const k = cursorKeys.current.get(firstCursor)
    const i = k === undefined ? -1 : model.buckets.findIndex((b) => b.key === k)
    if (i >= 0) win = i
  }

  // height per item, measured once per layout (and refined while few items
  // are loaded), so the estimated space doesn't wobble as pages arrive
  const per = useRef({ key: '', value: 0, basis: 0 })
  const candidate = perItemHeight(rows, gap, { size: tileSize, avail })
  if (per.current.key !== layoutKey || (per.current.basis < 200 && items.length > per.current.basis + 20)) {
    per.current = { key: layoutKey, value: candidate, basis: items.length }
  }
  const perItem = per.current.value || candidate

  const loadedHeight = rows.reduce((a, r) => a + r.height + gap, 0)
  const geom = model ? geometry(model, win, items.length, loadedHeight, perItem, !!result.hasNextPage) : null

  // row bookkeeping for offset <-> position
  const rowFirst = useMemo(() => rows.map((r) => r.tiles[0]?.index ?? 0), [rows])
  const rowHeights = useMemo(() => rows.map((r) => r.height + gap), [rows, gap])
  const startsAbs = useMemo(
    () => opts.rowStartsLoaded.map((s) => s + (geom?.top ?? 0)),
    [opts.rowStartsLoaded, geom?.top], // eslint-disable-line react-hooks/exhaustive-deps
  )

  const live = useRef({ model, geom, win, startsAbs, rowFirst, rowHeights, items, result, query })
  live.current = { model, geom, win, startsAbs, rowFirst, rowHeights, items, result, query }

  const positionAt = useCallback((y: number) => {
    const L = live.current
    return L.geom ? positionAtOffset(L.geom, y, L.startsAbs, L.rowFirst, L.rowHeights) : 0
  }, [])
  const offsetOf = useCallback((pos: number) => {
    const L = live.current
    return L.geom ? offsetOfPosition(L.geom, pos, L.startsAbs, L.rowFirst) : 0
  }, [])

  const busy = useRef(false)
  const key = useMemo(() => ['items', query] as const, [query])

  /** Replace the window with one around bucket b's month and show list
   * position `target` (that month's start unless given). */
  const jump = useCallback(
    async (b: number, target?: number) => {
      const L = live.current
      const el = opts.scrollEl()
      if (!L.model || !L.geom) return
      const q = L.query
      // the list's last months: start earlier so the window fills the screen
      const capacity = ((el?.clientHeight ?? 800) / Math.max(1, L.geom.perItem)) * 1.5
      const w = fitBucket(L.model, b, capacity)
      await qc.cancelQueries({ queryKey: key, exact: true })
      const cursor = w === 0 ? '' : L.model.buckets[w].cursor
      const page = await api.listItemsFrom(q, cursor, Math.min(500, Math.max(120, Math.ceil(capacity))))
      if (live.current.query !== q) return
      opts.setAnchor({ kind: 'pos', pos: target ?? L.model.starts[b] })
      qc.setQueryData<Pages>(key, { pages: [{ ...page, total: L.model.total }], pageParams: [cursor] })
    },
    [qc, key], // eslint-disable-line react-hooks/exhaustive-deps
  )

  /** Load the month before the window and put it in front, keeping the
   * rows on screen where they are. */
  const prepend = useCallback(async () => {
    const L = live.current
    const el = opts.scrollEl()
    if (!L.model || !L.geom || L.win <= 0 || !el) return
    const q = L.query
    const b = L.win - 1
    const cursor = b === 0 ? '' : L.model.buckets[b].cursor
    const have = new Set(L.items.map((i) => i.id))
    const pages: ItemPage[] = []
    const params: string[] = []
    let cur = cursor
    const limit = Math.min(500, L.model.buckets[b].count + 1)
    for (let n = 0; n < 40; n++) {
      const page = await api.listItemsFrom(q, cur, limit)
      if (live.current.query !== q) return
      const { items: kept, met } = untilOverlap(page.items, have)
      pages.push({ ...page, items: kept })
      params.push(cur)
      if (met || !page.next_cursor) break
      cur = page.next_cursor
    }
    if (live.current.query !== q || live.current.win !== L.win) return
    pages[0] = { ...pages[0], total: L.model.total }
    // keep the first loaded row (or the one at the top) where it is
    const M = live.current
    const y = el.scrollTop
    const r = M.startsAbs.length ? Math.max(0, M.startsAbs.findIndex((s, i) => s + M.rowHeights[i] > y)) : -1
    const first = r >= 0 ? M.items[M.rowFirst[r]] : undefined
    if (first) opts.setAnchor({ kind: 'item', id: first.id, frac: 0, pointY: M.startsAbs[r] - y })
    qc.setQueryData<Pages>(key, (old) =>
      old
        ? {
            pages: [...pages, ...old.pages.map((p, i) => (i === 0 ? { ...p, total: null } : p))],
            pageParams: [...params, ...old.pageParams],
          }
        : old,
    )
  }, [qc, key]) // eslint-disable-line react-hooks/exhaustive-deps

  const check = useCallback(() => {
    const L = live.current
    const el = opts.scrollEl()
    if (!el || !L.model || !L.geom || busy.current) return
    const act = loadAction(L.model, L.geom, L.win, el.scrollTop, el.clientHeight, !!L.result.hasNextPage)
    if (act.kind === 'none') return
    if (act.kind === 'next') {
      if (!L.result.isFetchingNextPage) void L.result.fetchNextPage()
      return
    }
    busy.current = true
    const run = act.kind === 'prepend' ? prepend() : jump(act.bucket)
    run
      .catch(() => {
        // the list shows its usual error UI on its own fetches; a failed
        // jump leaves the window as it was
      })
      .finally(() => {
        busy.current = false
      })
  }, [jump, prepend]) // eslint-disable-line react-hooks/exhaustive-deps

  const goTo = useCallback(
    (pos: number) => {
      const L = live.current
      const el = opts.scrollEl()
      if (!L.model || !L.geom || !el) return
      if (pos >= L.geom.before && pos < L.geom.before + L.geom.loaded) {
        el.scrollTop = offsetOf(pos)
        return
      }
      const b = Math.max(0, bucketAt(L.model, pos))
      busy.current = true
      jump(b, L.model.starts[b])
        .catch(() => {})
        .finally(() => {
          busy.current = false
        })
    },
    [jump, offsetOf], // eslint-disable-line react-hooks/exhaustive-deps
  )

  // after each change of the loaded list, see whether the viewport needs more
  useEffect(() => {
    const t = setTimeout(check, 30)
    return () => clearTimeout(t)
  }, [items.length, geom?.top, model, check])

  return {
    model,
    geom,
    padStart: geom?.top ?? 0,
    padEnd: geom?.bottom ?? 0,
    positionAt,
    offsetOf,
    check,
    goTo,
  }
}
