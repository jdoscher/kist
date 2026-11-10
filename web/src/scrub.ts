// The main grid's date scrubber (Kist 2.0 M6): month buckets from
// GET /api/items/buckets, the honest scroll height they give, and the maths
// between scroll offsets, list positions and months. Pure; tests/scrub.test.ts.
//
// Model: the grid's list is "a window" of the full list that starts at the
// first item of one month (bucket) and runs on for as many pages as are
// loaded. The months before the window and the items after it take their
// estimated height (count × the average height per item of the loaded
// rows), so the scrollbar and the rail cover the whole list. Jumping
// replaces the window with one that starts at the target month's cursor;
// scrolling up past the window's start loads the month before it.

import type { Query } from './api.ts'

export interface Bucket {
  /** "2024-06", or null for items with no date (date taken) */
  key: string | null
  count: number
  /** GET /api/items?cursor= starts at this month's first item */
  cursor: string
}

export interface Buckets {
  sort: string
  order: 'asc' | 'desc'
  total: number
  buckets: Bucket[]
}

export type DateSort = 'created' | 'modified' | 'taken'

/** The item list's date sorts (search.ORDERS) as the buckets endpoint's
 * sort + order. Other sorts (name, size, rating) have no scrubber. */
const DATE_ORDERS: Record<string, { sort: DateSort; order: 'asc' | 'desc' }> = {
  '-created': { sort: 'created', order: 'desc' },
  created: { sort: 'created', order: 'asc' },
  '-modified': { sort: 'modified', order: 'desc' },
  modified: { sort: 'modified', order: 'asc' },
  '-taken': { sort: 'taken', order: 'desc' },
  taken: { sort: 'taken', order: 'asc' },
}

/** The bucket sort for a list, or null when it has no scrubber: not sorted
 * by a date, or a ranked search (best match first, not by date). */
export function scrubSort(
  q: Pick<Query, 'order' | 'semantic' | 'similar' | 'color' | 'text' | 'image'>,
): { sort: DateSort; order: 'asc' | 'desc' } | null {
  // search.isRanked, inline: api.ts imports this module
  if (q.semantic || q.similar || q.color || q.text || q.image) return null
  return DATE_ORDERS[q.order] ?? null
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** "2024-06" -> "June 2024"; null -> "No date". */
export function monthLabel(key: string | null): string {
  if (!key) return 'No date'
  const m = /^(\d{4})-(\d{2})$/.exec(key)
  if (!m) return key
  const month = MONTHS[Number(m[2]) - 1]
  return month ? `${month} ${m[1]}` : key
}

export function yearOf(key: string | null): string | null {
  return key ? key.slice(0, 4) : null
}

export interface ScrubModel {
  buckets: Bucket[]
  /** list position (0-based) of each bucket's first item */
  starts: number[]
  total: number
}

export function scrubModel(b: Pick<Buckets, 'buckets'>): ScrubModel {
  const starts: number[] = []
  let n = 0
  for (const x of b.buckets) {
    starts.push(n)
    n += Math.max(0, x.count)
  }
  return { buckets: b.buckets, starts, total: n }
}

/** The bucket holding list position `pos` (clamped to the ends). */
export function bucketAt(m: ScrubModel, pos: number): number {
  if (m.buckets.length === 0) return -1
  let lo = 0
  let hi = m.starts.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (m.starts[mid] <= pos) lo = mid
    else hi = mid - 1
  }
  return lo
}

/** Which bucket the loaded window starts at: the one whose cursor the
 * first page was fetched from ('' = the list's start). */
export function windowStart(m: ScrubModel, firstCursor: string | null | undefined): number {
  if (!firstCursor) return 0
  const i = m.buckets.findIndex((b) => b.cursor === firstCursor)
  return i < 0 ? 0 : i
}

export interface Geometry {
  /** estimated px of the months before the window */
  top: number
  /** px of the loaded rows */
  loadedHeight: number
  /** estimated px of the items after the loaded ones (0 at the list's end) */
  bottom: number
  perItem: number
  /** list position of the window's first item */
  before: number
  loaded: number
}

export function geometry(
  m: ScrubModel,
  win: number,
  loaded: number,
  loadedHeight: number,
  perItem: number,
  hasMore: boolean,
): Geometry {
  const before = win > 0 && win < m.starts.length ? m.starts[win] : 0
  const rest = hasMore ? Math.max(0, m.total - before - loaded) : 0
  return { top: Math.round(before * perItem), loadedHeight, bottom: Math.round(rest * perItem), perItem, before, loaded }
}

/**
 * Average height one item takes in these rows (row height + gap, over the
 * tiles in it), leaving out the last row, which may be partial. Falls back
 * to one square tile per `fallbackCols` when there are too few rows.
 */
export function perItemHeight(
  rows: { height: number; tiles: unknown[] }[],
  gap: number,
  fallback: { size: number; avail: number },
): number {
  const full = rows.length > 1 ? rows.slice(0, -1) : []
  const tiles = full.reduce((a, r) => a + r.tiles.length, 0)
  if (tiles >= 6) return full.reduce((a, r) => a + r.height + gap, 0) / tiles
  const cell = fallback.size + gap
  const cols = Math.max(1, Math.floor(fallback.avail / cell))
  return cell / cols
}

/** Binary search: the last row whose start is <= y (0 when y is above all). */
export function rowAtOffset(starts: number[], y: number): number {
  let lo = 0
  let hi = starts.length - 1
  if (hi < 0) return -1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (starts[mid] <= y) lo = mid
    else hi = mid - 1
  }
  return lo
}

/**
 * The list position at scroll offset `y` (fractional). `rowStarts` are the
 * loaded rows' tops (content px, after `g.top`), `rowFirst` each row's first
 * item index within the window, `rowHeights` their height + gap.
 */
export function positionAtOffset(
  g: Geometry,
  y: number,
  rowStarts: number[],
  rowFirst: number[],
  rowHeights: number[],
): number {
  if (y < g.top) return Math.max(0, y / g.perItem)
  const end = g.top + g.loadedHeight
  if (y >= end || rowStarts.length === 0) return g.before + g.loaded + (y - end) / g.perItem
  const r = rowAtOffset(rowStarts, y)
  const nextFirst = r + 1 < rowFirst.length ? rowFirst[r + 1] : g.loaded
  const within = rowHeights[r] > 0 ? (y - rowStarts[r]) / rowHeights[r] : 0
  return g.before + rowFirst[r] + within * (nextFirst - rowFirst[r])
}

/** The scroll offset of list position `pos`: inside the window, its row's
 * top; outside it, the estimate. */
export function offsetOfPosition(
  g: Geometry,
  pos: number,
  rowStarts: number[],
  rowFirst: number[],
): number {
  if (pos < g.before || rowStarts.length === 0) return Math.round(Math.min(pos, g.before) * g.perItem)
  const i = pos - g.before
  if (i >= g.loaded) return Math.round(g.top + g.loadedHeight + (i - g.loaded) * g.perItem)
  // the row holding window index i
  let lo = 0
  let hi = rowFirst.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (rowFirst[mid] <= i) lo = mid
    else hi = mid - 1
  }
  return rowStarts[lo]
}

/**
 * The bucket to start a window at so it fills the screen: `b` itself,
 * unless fewer than `capacity` items follow its start (the list's last
 * months), then the latest bucket that leaves `capacity` items to show.
 */
export function fitBucket(m: ScrubModel, b: number, capacity: number): number {
  if (b <= 0) return 0
  const latest = Math.max(0, m.total - Math.ceil(capacity))
  if (m.starts[b] <= latest) return b
  return Math.min(b, Math.max(0, bucketAt(m, latest)))
}

/**
 * Where the rail's thumb sits: the list position at the top of the screen,
 * moving on to the list's end over the last screenful (the last months
 * can't scroll to the top of the screen).
 */
export function railPosition(topPos: number, total: number, scrollTop: number, maxScroll: number, vh: number): number {
  const left = maxScroll - scrollTop
  if (vh <= 0 || left >= vh) return topPos
  const f = 1 - Math.max(0, left) / vh
  return Math.min(total - 0.001, topPos + (total - 0.001 - topPos) * f)
}

export type LoadAction =
  | { kind: 'none' }
  | { kind: 'next' }
  | { kind: 'prepend' }
  | { kind: 'jump'; bucket: number }

/**
 * What the grid should load for viewport [y, y+vh): the window's own next
 * page when the viewport nears the loaded end, the month before the window
 * when it shows the space just above it, or a new window at the month on
 * screen when it is well away from anything loaded.
 */
export function loadAction(m: ScrubModel, g: Geometry, win: number, y: number, vh: number, hasMore: boolean): LoadAction {
  const end = g.top + g.loadedHeight
  const far = 2 * vh
  if (g.loaded > 0 && y + vh <= g.top - far) return { kind: 'jump', bucket: bucketAt(m, y / g.perItem) }
  if (win > 0 && y < g.top) {
    if (g.loaded === 0) return { kind: 'jump', bucket: bucketAt(m, y / g.perItem) }
    return { kind: 'prepend' }
  }
  if (hasMore && y >= end + far) {
    return { kind: 'jump', bucket: bucketAt(m, g.before + g.loaded + (y - end) / g.perItem) }
  }
  if (hasMore && y + vh >= end - vh) return { kind: 'next' }
  return { kind: 'none' }
}

export interface YearTick {
  label: string
  /** 0..1 down the rail */
  at: number
  bucket: number
}

/** One tick where each year (and the no-date bucket) starts, dropping
 * ticks closer than `minGap` (0..1 of the rail) to the last one kept. */
export function yearTicks(m: ScrubModel, minGap = 0): YearTick[] {
  const out: YearTick[] = []
  let prev: string | null | undefined
  m.buckets.forEach((b, i) => {
    const y = b.key ? yearOf(b.key) : null
    if (i > 0 && y === prev) return
    prev = y
    const at = m.total ? m.starts[i] / m.total : 0
    const last = out.at(-1)
    if (last && at - last.at < minGap) return
    out.push({ label: y ?? 'No date', at, bucket: i })
  })
  return out
}

/** The bucket a slider key moves to from `i`: arrows a month, Page keys a
 * year, Home/End the ends. null for other keys. */
export function sliderKey(m: ScrubModel, i: number, key: string): number | null {
  const n = m.buckets.length
  if (n === 0) return null
  const clamp = (x: number) => Math.min(n - 1, Math.max(0, x))
  switch (key) {
    case 'ArrowDown':
    case 'ArrowRight':
      return clamp(i + 1)
    case 'ArrowUp':
    case 'ArrowLeft':
      return clamp(i - 1)
    case 'Home':
      return 0
    case 'End':
      return n - 1
    case 'PageDown': {
      const y = yearOf(m.buckets[i]?.key ?? null)
      let j = i + 1
      while (j < n && yearOf(m.buckets[j].key) === y) j++
      return clamp(j)
    }
    case 'PageUp': {
      const y = yearOf(m.buckets[i]?.key ?? null)
      let j = i
      // to the start of this year, or of the year before when already there
      while (j > 0 && yearOf(m.buckets[j - 1].key) === y) j--
      if (j === i) {
        j = i - 1
        const py = j >= 0 ? yearOf(m.buckets[j].key) : null
        while (j > 0 && yearOf(m.buckets[j - 1].key) === py) j--
      }
      return clamp(j)
    }
    default:
      return null
  }
}

/** Query string for GET /api/items/buckets: the list's own filters, with
 * the date sort as sort + order instead of the list's `order`. */
export function bucketParams(listParams: URLSearchParams, s: { sort: DateSort; order: 'asc' | 'desc' }): URLSearchParams {
  const p = new URLSearchParams(listParams)
  p.delete('order')
  p.delete('limit')
  p.delete('cursor')
  p.set('sort', s.sort)
  p.set('order', s.order)
  return p
}

/** Drop items of `page` from the first one already in the window: a page
 * fetched from the month before the window runs on into it. Returns the
 * kept items and whether it reached the window. */
export function untilOverlap<T extends { id: string }>(items: T[], have: Set<string>): { items: T[]; met: boolean } {
  const i = items.findIndex((x) => have.has(x.id))
  return i < 0 ? { items, met: false } : { items: items.slice(0, i), met: true }
}
