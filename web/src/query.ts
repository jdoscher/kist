// Query helpers shared by the sidebar, top bar and app state (no React).
import type { Item, Query } from './api'

// "Unfiled" is the inbox: items in no folder AND with no tags. The server
// defines it (GET /api/items?view=unfiled, MCP search_items(view="unfiled")),
// so the UI and agents always mean the same list. Every fresh visit opens here.
export const unfiledQuery: Partial<Query> = { view: 'unfiled', deleted: false }

export function isUnfiledView(q: Query): boolean {
  return q.view === 'unfiled' && !q.deleted
}

/** Picking a folder, a tag or Trash leaves the Unfiled view; searching or
 * changing the type, rating or sort inside it stays there. */
export function mergeQuery(prev: Query, patch: Partial<Query>): Query {
  const next = { ...prev, ...patch }
  if (prev.view === 'unfiled' && !('view' in patch)) {
    const leaving =
      next.folder !== null || next.deleted || next.tags.length > 0 || next.tags_any.length > 0
    if (leaving) next.view = null
  }
  return next
}

/**
 * Whether an item that was just edited still belongs in a view, so an edit
 * can update the loaded list in place instead of refetching every page
 * (measured: 37 pages, 4.5 MB, 1.5 s on the LAN). Answers "yes" when it
 * can't tell (text search), and the list refreshes on the next visit.
 * `folderSubtree`: the viewed folder plus all its subfolders.
 */
export function stillMatches(item: Item, q: Query, folderSubtree?: Set<string>): boolean {
  if (q.deleted ? item.state !== 'trashed' : item.state !== 'active') return false
  const tags = new Set(item.tags.map((t) => t.toLowerCase()))
  if (q.view === 'unfiled' && (item.tags.length > 0 || item.folder_ids.length > 0)) return false
  if (q.tags.some((t) => !tags.has(t.toLowerCase()))) return false
  if (q.tags_any.length && !q.tags_any.some((t) => tags.has(t.toLowerCase()))) return false
  if (q.untagged && item.tags.length > 0) return false
  if (q.folder === 'none' && item.folder_ids.length > 0) return false
  if (q.folder && q.folder !== 'none') {
    const inside = folderSubtree ?? new Set([q.folder])
    if (!item.folder_ids.some((f) => inside.has(f))) return false
  }
  if (q.type && item.kind !== q.type) return false
  if (q.rating && item.rating < q.rating) return false
  if (q.preview && item.preview_status !== q.preview) return false
  return true
}

/** A folder and every folder below it. */
export function folderSubtree(
  rootId: string,
  folders: { id: string; parent_id: string | null }[],
): Set<string> {
  const out = new Set([rootId])
  let grew = true
  while (grew) {
    grew = false
    for (const f of folders) {
      if (f.parent_id && out.has(f.parent_id) && !out.has(f.id)) {
        out.add(f.id)
        grew = true
      }
    }
  }
  return out
}
