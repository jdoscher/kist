// Query helpers shared by the sidebar, top bar and app state (no React).
import type { Query } from './api'

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
