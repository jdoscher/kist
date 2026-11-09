// Where you are in Kist lives in the URL, so a refresh keeps your place,
// Back closes the preview / item sheet / menu, and links (including the
// web_url agents get from MCP) open the exact view or item.
//
//   /                 Unfiled (the inbox; also /unfiled)
//   /all              everything
//   /trash            the trash
//   /folder/:id       a folder (and its subfolders)
//   /tag/:name        one tag
//   /smart/:id        a smart folder (its filters ride along as params)
//   ?item=:id         the item shown in the info panel / item sheet
//   &full=1           ...opened full screen
//
// Other filters are params: q, tags, any, untagged, folder (none), preview,
// type, rating, order, after, before. Pure functions, tested in tests/url.test.ts.

import { emptyQuery, type PreviewStatus, type Query } from './api.ts'

export interface Place {
  query: Query
  smartId: string | null
  item: string | null
  full: boolean
}

export type Layer = 'view' | 'sheet' | 'full'

const RANK: Record<Layer, number> = { view: 0, sheet: 1, full: 2 }

const PREVIEWS: PreviewStatus[] = ['pending', 'ready', 'failed', 'none']

/** The view part of the path (no item). */
export function placePath(p: Place): string {
  const q = p.query
  if (q.deleted) return '/trash'
  if (p.smartId) return `/smart/${encodeURIComponent(p.smartId)}`
  if (q.view === 'unfiled') return '/'
  if (q.folder && q.folder !== 'none') return `/folder/${encodeURIComponent(q.folder)}`
  if (q.tags.length === 1) return `/tag/${encodeURIComponent(q.tags[0])}`
  return '/all'
}

export function toUrl(p: Place): string {
  const q = p.query
  const path = placePath(p)
  const s = new URLSearchParams()
  const tagInPath = path.startsWith('/tag/')
  if (q.q) s.set('q', q.q)
  if (q.tags.length && !tagInPath) s.set('tags', q.tags.join(','))
  if (q.tags_any.length) s.set('any', q.tags_any.join(','))
  if (q.untagged) s.set('untagged', '1')
  // a folder is in the path unless trash or a smart folder took the path
  if (q.folder === 'none' || (q.folder && !path.startsWith('/folder/'))) s.set('folder', q.folder)
  if (q.view === 'unfiled' && path !== '/') s.set('view', 'unfiled')
  if (q.preview) s.set('preview', q.preview)
  if (q.type) s.set('type', q.type)
  if (q.rating) s.set('rating', String(q.rating))
  if (q.order !== emptyQuery.order) s.set('order', q.order)
  if (q.created_after) s.set('after', q.created_after)
  if (q.created_before) s.set('before', q.created_before)
  if (p.item) s.set('item', p.item)
  if (p.item && p.full) s.set('full', '1')
  const qs = s.toString()
  return qs ? `${path}?${qs}` : path
}

function list(v: string | null): string[] {
  return v ? v.split(',').map((t) => t.trim()).filter(Boolean) : []
}

export function fromUrl(pathname: string, search: string): Place {
  const s = new URLSearchParams(search)
  const parts = pathname.split('/').filter(Boolean).map(decodeURIComponent)
  const query: Query = {
    ...emptyQuery,
    q: s.get('q') ?? '',
    tags: list(s.get('tags')),
    tags_any: list(s.get('any')),
    untagged: s.get('untagged') === '1',
    folder: s.get('folder') || null,
    view: s.get('view') === 'unfiled' ? 'unfiled' : null,
    preview: PREVIEWS.includes(s.get('preview') as PreviewStatus)
      ? (s.get('preview') as PreviewStatus)
      : null,
    type: s.get('type') || null,
    rating: Math.max(0, Math.min(5, Number(s.get('rating')) || 0)),
    order: s.get('order') || emptyQuery.order,
    created_after: s.get('after') || null,
    created_before: s.get('before') || null,
  }
  let smartId: string | null = null
  const [head, arg] = parts
  if (!head || head === 'unfiled') query.view = 'unfiled'
  else if (head === 'trash') query.deleted = true
  else if (head === 'folder' && arg) query.folder = arg
  else if (head === 'tag' && arg) query.tags = [arg, ...query.tags.filter((t) => t !== arg)]
  else if (head === 'smart' && arg) smartId = arg
  // anything else (including /all) is the whole library
  const item = s.get('item') || null
  return { query, smartId, item, full: !!item && s.get('full') === '1' }
}

/** Which layer a place shows. On a phone the item sheet is its own layer, so
 * Back closes it; on desktop the info panel is part of the view. */
export function layerOf(p: Place, mobile: boolean): Layer {
  if (p.item && p.full) return 'full'
  if (p.item && mobile) return 'sheet'
  return 'view'
}

export type HistoryAction = 'none' | 'push' | 'replace' | 'back'

/**
 * How moving from `cur` to `next` should touch browser history.
 * `curPushed`: the current entry was pushed as a layer (sheet / full / menu),
 * so closing that layer should go Back rather than add an entry.
 * `curIsMenu`: the current entry is the open folder menu (phone).
 */
export function historyAction(
  cur: Place,
  next: Place,
  opts: { mobile: boolean; curPushed: boolean; curIsMenu?: boolean },
): HistoryAction {
  if (toUrl(cur) === toUrl(next)) return 'none'
  if (placePath(cur) !== placePath(next)) {
    // picking a view from the menu replaces the menu's entry
    return opts.curIsMenu ? 'replace' : 'push'
  }
  const from = RANK[layerOf(cur, opts.mobile)]
  const to = RANK[layerOf(next, opts.mobile)]
  if (to > from) return 'push'
  if (to < from && opts.curPushed) return 'back'
  return 'replace'
}
