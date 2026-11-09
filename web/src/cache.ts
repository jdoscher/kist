// The write rule (no React; tests/cache.test.ts): the API answers every edit
// with the updated item, and that answer is patched into every loaded list
// in place. Refetching instead reloads every loaded page in sequence
// (measured: 37 pages, 4.5 MB). data.ts's useApplyItems applies this to the
// query cache.

import type { Item, Query } from './api.ts'
import { folderSubtree, stillMatches } from './query.ts'

export interface ItemsPage {
  items: Item[]
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
