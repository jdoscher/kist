// Query helpers shared by the sidebar, top bar and app state (no React).
import { emptyQuery, type Item, type Query } from './api.ts'
import { hasM45Filters, m45Filters } from './m45/filters.ts'

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
  if (q.flag && (item.flag ?? 'none') !== q.flag) return false
  if (q.label && (item.label ?? 'none') !== q.label) return false
  // fixity: the server's call (a re-check clears it); keep the item
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
  const merged: Query = {
    ...emptyQuery,
    ...q,
    deleted: !!q.deleted,
    // the server stores unset Milestone 2 fields as null
    semantic: q.semantic ?? '',
    text: q.text ?? '',
    color_tol: q.color_tol ?? emptyQuery.color_tol,
  }
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
    // Milestone 2: the server saves these too (not similar or an image)
    semantic: q.semantic,
    color: q.color,
    color_tol: q.color_tol,
    text: q.text,
    person: q.person,
    flag: q.flag ?? null,
    label: q.label ?? null,
    // Milestones 4 and 5 (the server saves them under the same names)
    ...m45Filters(q),
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

/** Quiet tiles (B1): a tile shows its rating on hover, focus and
 * selection, and every tile shows it while the grid is filtered or sorted
 * by rating (the moment ratings are what you're looking at). */
export function ratingsShown(q: Pick<Query, 'rating' | 'order'>): boolean {
  return q.rating > 0 || q.order === '-rating'
}

/** Filters that narrow the view (search text, type, rating...) as opposed
 * to the view itself (Unfiled, a folder, tags, the trash). */
export function hasNarrowing(q: Query): boolean {
  return (
    !!q.q ||
    !!q.type ||
    q.rating > 0 ||
    !!q.preview ||
    !!q.fixity ||
    !!q.created_after ||
    !!q.created_before ||
    q.untagged ||
    q.tags_any.length > 0 ||
    !!q.semantic ||
    !!q.similar ||
    !!q.color ||
    !!q.text ||
    !!q.person ||
    !!q.image ||
    !!q.flag ||
    !!q.label ||
    hasM45Filters(q)
  )
}

/** The same search and filters over the whole library ("Search everything"). */
export function everywhere(q: Query): Query {
  return { ...q, view: null, folder: null, tags: [], deleted: false }
}

export type EmptyAction = 'everywhere' | 'all' | null

/**
 * What an empty grid says (B17: one serif line, one dim line, one action),
 * so an empty search doesn't tell you to upload:
 * - an empty library says how to add things (no drag and drop on a phone)
 * - a search with no hits names the view and offers "Search everything"
 *   when the view is narrower than the whole library
 * - an empty Unfiled says everything is filed; empty Picks and Rejects
 *   say where picks come from
 * - an empty folder, tag or smart folder says how things get there
 */
export function emptyState(
  q: Query,
  opts: { libraryEmpty: boolean; mobile: boolean; title: string; smart?: boolean },
): { text: string; hint: string; action: EmptyAction } {
  if (q.deleted && !opts.smart)
    return { text: 'Trash is empty.', hint: 'Trashed items wait here until you restore them or delete them for good.', action: null }
  if (opts.libraryEmpty && !q.deleted)
    return {
      text: 'Your library is empty.',
      hint: opts.mobile
        ? 'Open the menu and tap Upload to add photos from this phone.'
        : 'Drop files anywhere, paste an image, or use Upload.',
      action: null,
    }
  const narrowView = q.view !== null || q.folder !== null || q.tags.length > 0 || q.deleted || !!opts.smart
  // Picks and Rejects (the sidebar's Cull rows) are flag filters with
  // their own words when nothing else narrows them
  if (q.flag && q.flag !== 'none' && !narrowView && !hasNarrowing({ ...q, flag: null }))
    return q.flag === 'pick'
      ? { text: 'No picks yet.', hint: 'Pick items in Triage, Survey or the info panel and they gather here.', action: 'all' }
      : { text: 'No rejects.', hint: 'Items you reject in Triage, Survey or the info panel gather here, ready to trash.', action: 'all' }
  if (hasNarrowing(q)) {
    const words = q.q || q.semantic || q.text
    const what = words ? `matches “${words}”` : 'matches these filters'
    return {
      text: q.view === null && !narrowView ? `Nothing ${what}.` : `Nothing in ${opts.title} ${what}.`,
      hint: narrowView ? 'The whole library might have it.' : words ? 'Try other words, or describe it another way.' : 'Try fewer filters.',
      action: narrowView ? 'everywhere' : null,
    }
  }
  if (q.view === 'unfiled')
    return {
      text: 'Nothing in Unfiled: every item has a folder or a tag.',
      hint: 'Items with no folder and no tag land here. They stay in All items too.',
      action: 'all',
    }
  const hint = opts.smart
    ? 'It fills as items match its search.'
    : q.folder
      ? opts.mobile
        ? 'Add items to it from their info sheet.'
        : 'Drag items onto it in the sidebar, or add them from the info panel.'
      : q.tags.length
        ? 'Items show here once they have this tag.'
        : ''
  return { text: `Nothing in ${opts.title} yet.`, hint, action: narrowView ? 'all' : null }
}

/**
 * The sidebar's count for the view on screen, when it has one: All items
 * and Trash (library stats) and a folder (its item_count, subfolders
 * included, as the grid shows them). Every item counts one, while the grid
 * shows a closed stack as one tile, so the two can differ. null for any
 * search, filter, tag or smart folder (no sidebar number to agree with).
 */
export function sidebarCount(
  q: Query,
  src: { smart?: boolean; stats?: { total_items: number; trashed_items: number } | null; folders?: { id: string; item_count: number }[] | null },
): number | null {
  if (src.smart || hasNarrowing(q) || q.view !== null || q.tags.length > 0) return null
  if (q.deleted) return q.folder ? null : (src.stats?.trashed_items ?? null)
  if (q.folder === 'none') return null
  if (q.folder) return src.folders?.find((f) => f.id === q.folder)?.item_count ?? null
  return src.stats?.total_items ?? null
}

/**
 * The grid header's count (A6). It leads with the same number as the
 * sidebar (every item), and says how many tiles that is when closed stacks
 * make fewer: "43 items · 41 tiles". With no sidebar count (a search) it's
 * the number of results.
 */
export function gridCountText(tiles: number, items: number | null): string {
  const n = (v: number, w: string) => `${v.toLocaleString('en-US')} ${w}${v === 1 ? '' : 's'}`
  if (items === null || items <= tiles) return n(tiles, 'item')
  return `${n(items, 'item')} · ${n(tiles, 'tile')}`
}
