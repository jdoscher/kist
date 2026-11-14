// The write rule (no React; tests/cache.test.ts): the API answers every edit
// with the updated item, and that answer is patched into every loaded list
// in place. Refetching instead reloads every loaded page in sequence
// (measured: 37 pages, 4.5 MB). data.ts's useApplyItems applies this to the
// query cache.
//
// One exception: in a list sorted by the field that changed (rating while
// sorted by rating, a rename while sorted by name, any edit while sorted by
// last edit) the item's place is no longer known, and the next page's
// cursor would return it again (a duplicate tile) or skip it. That list is
// refetched (needsRefetch); every other list is still patched in place.

import type { GridItem, Item, Query } from './api.ts'
import { folderSubtree, stillMatches } from './query.ts'

export interface ItemsPage {
  /** slim list items (fields=grid), or full ones an edit patched in */
  items: GridItem[]
  next_cursor: string | null
  total: number | null
}

export interface ItemsPages {
  pages: ItemsPage[]
  pageParams: unknown[]
}

/**
 * One loaded list after some items changed: changed items are replaced,
 * items that no longer belong in the view (or were deleted) are dropped,
 * and the total shrinks by what was dropped. Items new to the view are not
 * inserted (their position depends on the sort): the list is marked stale
 * and the next visit reloads it.
 */
export function patchPages(
  data: ItemsPages,
  query: Query,
  updated: Map<string, Item>,
  removed: Set<string>,
  folders: { id: string; parent_id: string | null }[],
): ItemsPages {
  const sub = query.folder && query.folder !== 'none' ? folderSubtree(query.folder, folders) : undefined
  let dropped = 0
  let changed = false
  const pages = data.pages.map((p) => {
    const items = p.items.flatMap((i) => {
      if (removed.has(i.id)) {
        dropped++
        return []
      }
      const next = updated.get(i.id)
      if (!next) return [i]
      changed = true
      if (!stillMatches(next, query, sub)) {
        dropped++
        return []
      }
      return [next]
    })
    return { ...p, items }
  })
  if (!dropped && !changed) return data
  if (dropped && pages[0] && pages[0].total != null) pages[0] = { ...pages[0], total: pages[0].total - dropped }
  return { ...data, pages }
}

/** The item field each sort order (`order` minus its "-") sorts by. */
const SORT_FIELD: Record<string, (i: GridItem) => unknown> = {
  rating: (i) => i.rating,
  filename: (i) => i.filename,
  modified: (i) => i.modified_at,
  size: (i) => i.size_bytes,
  created: (i) => i.created_at,
}

/** Whether an edit changed the value `order` sorts by. */
export function sortKeyChanged(order: string, before: GridItem, after: GridItem): boolean {
  const field = SORT_FIELD[order.replace(/^-/, '')]
  return !!field && field(before) !== field(after)
}

/**
 * Whether a loaded list must be refetched after these edits: an item it
 * shows (and still should) moved in its sort order, so patching it in place
 * would leave it at a stale position (and a duplicate once the next page,
 * fetched by cursor, returns it again).
 */
export function needsRefetch(
  data: ItemsPages,
  query: Query,
  updated: Map<string, Item>,
  folders: { id: string; parent_id: string | null }[],
): boolean {
  const sub = query.folder && query.folder !== 'none' ? folderSubtree(query.folder, folders) : undefined
  return data.pages.some((p) =>
    p.items.some((i) => {
      const next = updated.get(i.id)
      return !!next && stillMatches(next, query, sub) && sortKeyChanged(query.order, i, next)
    }),
  )
}

/** The loaded pages as one list, each item once (a page fetched after an
 * item moved can repeat one already shown). */
export function flattenUnique<T extends { id: string }>(pages: { items: T[] }[] | undefined): T[] {
  if (!pages) return []
  const seen = new Set<string>()
  const out: T[] = []
  for (const p of pages) {
    for (const i of p.items) {
      if (seen.has(i.id)) continue
      seen.add(i.id)
      out.push(i)
    }
  }
  return out
}

/** Whether a list's (slim) copy of an item is newer than the full copy
 * cached for the info panel and preview (data.ts useItem refetches then):
 * a new preview, a preview that finished, or a later edit. */
export function listIsNewer(list: GridItem, full: Item): boolean {
  if (list.preview_rev > full.preview_rev) return true
  if (list.preview_status !== full.preview_status && full.preview_status === 'pending') return true
  return Date.parse(list.modified_at) > Date.parse(full.modified_at)
}
