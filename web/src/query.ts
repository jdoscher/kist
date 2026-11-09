// Query helpers shared by the sidebar, top bar and app state (no React).
import { emptyQuery, type Item, type Query } from './api.ts'

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

/** A smart folder's saved filters as a full query. Older smart folders
 * stored Unfiled as "no folder + untagged". */
export function normalizeSaved(q: Partial<Query>): Query {
  const merged: Query = { ...emptyQuery, ...q, deleted: !!q.deleted }
  if (merged.folder === 'none' && merged.untagged && !merged.view) {
    return { ...merged, view: 'unfiled', folder: null, untagged: false }
  }
  return merged
}

/** The filters a smart folder saves (the reverse of normalizeSaved),
 * including the preview filter and the trash (Kist 1.5 contract item 7). */
export function smartFolderQuery(q: Query): Partial<Query> {
  return {
    preview: q.preview,
    deleted: q.deleted,
    q: q.q,
    tags: q.tags,
    tags_any: q.tags_any,
    untagged: q.untagged,
    view: q.view,
    folder: q.folder,
    type: q.type,
    rating: q.rating,
    order: q.order,
    created_after: q.created_after,
    created_before: q.created_before,
  }
}

/** The name of what the grid shows, for headings, labels and messages. */
export function viewTitle(
  q: Query,
  names: { folder?: (id: string) => string | undefined; smart?: string | null } = {},
): string {
  if (names.smart) return names.smart
  if (q.deleted) return 'Trash'
  if (q.view === 'unfiled') return 'Unfiled'
  if (q.folder === 'none') return 'items in no folder'
  if (q.folder) return names.folder?.(q.folder) ?? 'this folder'
  if (q.tags.length) return q.tags.map((t) => `#${t}`).join(' ')
  return 'All items'
}

/** Filters that narrow the view (search text, type, rating...) as opposed
 * to the view itself (Unfiled, a folder, tags, the trash). */
export function hasNarrowing(q: Query): boolean {
  return (
    !!q.q ||
    !!q.type ||
    q.rating > 0 ||
    !!q.preview ||
    !!q.created_after ||
    !!q.created_before ||
    q.untagged ||
    q.tags_any.length > 0
  )
}

/** The same search and filters over the whole library ("Search everything"). */
export function everywhere(q: Query): Query {
  return { ...q, view: null, folder: null, tags: [], deleted: false }
}

export type EmptyAction = 'everywhere' | 'all' | null

/**
 * What an empty grid says, so an empty search doesn't tell you to upload:
 * - an empty library says how to add things (no drag and drop on a phone)
 * - a search with no hits names the view and offers "Search everything"
 *   when the view is narrower than the whole library
 * - an empty Unfiled says everything is filed
 */
export function emptyState(
  q: Query,
  opts: { libraryEmpty: boolean; mobile: boolean; title: string; smart?: boolean },
): { text: string; action: EmptyAction } {
  if (q.deleted && !opts.smart) return { text: 'Trash is empty.', action: null }
  if (opts.libraryEmpty && !q.deleted)
    return {
      text: opts.mobile
        ? 'Your library is empty. Tap ☰ and then Upload to add photos from this phone.'
        : 'Your library is empty. Drop files anywhere, paste an image, or use Upload.',
      action: null,
    }
  const narrowView = q.view !== null || q.folder !== null || q.tags.length > 0 || q.deleted || !!opts.smart
  if (hasNarrowing(q)) {
    const what = q.q ? `matches “${q.q}”` : 'matches these filters'
    return {
      text: q.view === null && !narrowView ? `Nothing ${what}.` : `Nothing in ${opts.title} ${what}.`,
      action: narrowView ? 'everywhere' : null,
    }
  }
  if (q.view === 'unfiled')
    return { text: 'Nothing in Unfiled: every item has a folder or a tag.', action: 'all' }
  return { text: `Nothing in ${opts.title} yet.`, action: narrowView ? 'all' : null }
}
