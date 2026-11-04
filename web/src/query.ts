// Query helpers shared by the sidebar, top bar and app state (no React).
import type { Query } from './api'

// "Unfiled" is the inbox: items in no folder AND with no tags. Tagging or
// filing an item takes it out. Every page load opens there.
export const unfiledQuery: Partial<Query> = { folder: 'none', untagged: true, deleted: false }

export function isUnfiledView(q: Query): boolean {
  return q.folder === 'none' && q.untagged && !q.deleted
}

/** Leaving the Unfiled view drops its built-in "untagged" condition, so it
 * never lingers as a hidden filter (and a tag click there isn't "untagged
 * AND tagged", which always matches nothing). */
export function mergeQuery(prev: Query, patch: Partial<Query>): Query {
  const next = { ...prev, ...patch }
  const leaving =
    next.folder !== 'none' || next.deleted || next.tags.length > 0 || next.tags_any.length > 0
  if (isUnfiledView(prev) && !('untagged' in patch) && leaving) next.untagged = false
  return next
}
