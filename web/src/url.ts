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
//   /settings/:section  Settings: tokens, archive (health), ai, vocabulary
//                     or cleanup (tag cleanup)
//   /organise/:section  Organise: duplicates, stacks, collections (suggested)
//                     or people
//   /boards           the boards list (Milestone 3)
//   /board/:id        a board's canvas
//   /triage           triage mode (?view=unfiled|all, ?folder=)
//   ?item=:id         the item shown in the info panel / item sheet
//   &full=1           ...opened full screen
//
// Other filters are params: q, tags, any, untagged, folder (none), preview,
// type, rating, order, after, before, trash (a smart folder over the trash),
// fixity (failed), flag (pick, reject, none), label (a colour, none); and the Milestone 2 searches: semantic, similar (an item
// id), color + tol, text, person, image (an image kept for this tab:
// imageSearch.ts). Unknown params are ignored, never an error.
// Pure functions, tested in tests/url.test.ts.

import { emptyQuery, isItemKind, type PreviewStatus, type Query } from './api.ts'
import { addM45Params, m45FromSearch } from './m45/filters.ts'
import { addProjectParams, projectFromSearch } from './projectFilters.ts'

export type SettingsSection = 'tokens' | 'archive' | 'ai' | 'vocabulary' | 'cleanup' | 'templates' | 'sharing' | 'import' | 'phone'
export const SETTINGS_SECTIONS: SettingsSection[] = ['tokens', 'archive', 'ai', 'vocabulary', 'cleanup', 'templates', 'sharing', 'import', 'phone']

export type OrganiseSection = 'duplicates' | 'stacks' | 'collections' | 'people'
export const ORGANISE_SECTIONS: OrganiseSection[] = ['duplicates', 'stacks', 'collections', 'people']

/** Full screens that replace the library (Milestone 3). */
export type Screen =
  | { kind: 'boards'; trash?: boolean }
  | { kind: 'board'; id: string }
  | { kind: 'triage'; view: 'unfiled' | 'all'; folder?: string | null }
  // Milestones 4 and 5: the timeline and the map (in the library's frame,
  // with the library's filters), and the bookmarklet's /add card
  | { kind: 'timeline'; group: 'day' | 'month' | 'year' }
  | { kind: 'map' }
  | { kind: 'add'; search: string }
  // F6: the projects list and one project's page (in the library's frame)
  | { kind: 'projects'; archived?: boolean }
  | { kind: 'project'; id: string }

/** Screens shown in the library's frame (top bar, sidebar) instead of the grid. */
export function inLibraryFrame(sc: Screen | null | undefined): boolean {
  return !!sc && (sc.kind === 'boards' || sc.kind === 'timeline' || sc.kind === 'map' || sc.kind === 'projects' || sc.kind === 'project')
}

export function screenUrl(sc: Screen): string {
  if (sc.kind === 'add') return `/add${sc.search}`
  if (sc.kind === 'timeline') return sc.group === 'month' ? '/timeline' : `/timeline?group=${sc.group}`
  if (sc.kind === 'map') return '/map'
  if (sc.kind === 'boards') return sc.trash ? '/boards?trash=1' : '/boards'
  if (sc.kind === 'board') return `/board/${encodeURIComponent(sc.id)}`
  if (sc.kind === 'projects') return sc.archived ? '/projects?archived=1' : '/projects'
  if (sc.kind === 'project') return `/project/${encodeURIComponent(sc.id)}`
  const s = new URLSearchParams()
  s.set('view', sc.view)
  if (sc.folder) s.set('folder', sc.folder)
  return `/triage?${s}`
}

export interface Place {
  query: Query
  smartId: string | null
  item: string | null
  full: boolean
  /** the Settings screen is shown (over the library, which keeps its place) */
  settings?: SettingsSection | null
  /** the Organise screen (duplicates, stacks, collections, people) */
  organise?: OrganiseSection | null
  /** a full screen (boards, a board, triage) is shown */
  screen?: Screen | null
}

export type Layer = 'view' | 'sheet' | 'full'

const RANK: Record<Layer, number> = { view: 0, sheet: 1, full: 2 }

const PREVIEWS: PreviewStatus[] = ['pending', 'ready', 'failed', 'none']
const LABEL_PARAMS = ['red', 'orange', 'yellow', 'green', 'blue', 'purple', 'none']

/** The view part of the path (no item). */
function placePath(p: Place): string {
  if (p.settings) return `/settings/${p.settings}`
  if (p.organise) return `/organise/${p.organise}`
  if (p.screen) return screenUrl(p.screen)
  const q = p.query
  // a smart folder keeps its path even when it searches the trash
  if (p.smartId) return `/smart/${encodeURIComponent(p.smartId)}`
  if (q.deleted) return '/trash'
  if (q.view === 'unfiled') return '/'
  if (q.folder && q.folder !== 'none') return `/folder/${encodeURIComponent(q.folder)}`
  if (q.tags.length === 1) return `/tag/${encodeURIComponent(q.tags[0])}`
  return '/all'
}

export function toUrl(p: Place): string {
  const q = p.query
  const path = placePath(p)
  // the timeline and the map keep the library's filters in their URL
  if (p.screen && (p.screen.kind === 'timeline' || p.screen.kind === 'map')) {
    const s = filterParams(q)
    if (p.screen.kind === 'timeline' && p.screen.group !== 'month') s.set('group', p.screen.group)
    const qs = s.toString()
    return qs ? `${path.split('?')[0]}?${qs}` : path.split('?')[0]
  }
  if (p.settings || p.organise || p.screen) return path
  const s = new URLSearchParams()
  const tagInPath = path.startsWith('/tag/')
  if (q.q) s.set('q', q.q)
  if (q.tags.length && !tagInPath) s.set('tags', q.tags.join(','))
  if (q.tags_any.length) s.set('any', q.tags_any.join(','))
  if (q.untagged) s.set('untagged', '1')
  // a folder is in the path unless trash or a smart folder took the path
  if (q.folder === 'none' || (q.folder && !path.startsWith('/folder/'))) s.set('folder', q.folder)
  if (q.view === 'unfiled' && path !== '/') s.set('view', 'unfiled')
  if (q.deleted && path !== '/trash') s.set('trash', '1')
  if (q.preview) s.set('preview', q.preview)
  if (q.fixity) s.set('fixity', q.fixity)
  if (q.type) s.set('type', q.type)
  if (q.rating) s.set('rating', String(q.rating))
  if (q.order !== emptyQuery.order) s.set('order', q.order)
  if (q.created_after) s.set('after', q.created_after)
  if (q.created_before) s.set('before', q.created_before)
  if (q.semantic) s.set('semantic', q.semantic)
  if (q.similar) s.set('similar', q.similar)
  if (q.color) {
    s.set('color', q.color)
    if (q.color_tol !== emptyQuery.color_tol) s.set('tol', String(q.color_tol))
  }
  if (q.text) s.set('text', q.text)
  if (q.person) s.set('person', q.person)
  if (q.image) s.set('image', q.image)
  if (q.flag) s.set('flag', q.flag)
  if (q.label) s.set('label', q.label)
  addM45Params(q, s)
  addProjectParams(q, s)
  if (p.item) s.set('item', p.item)
  if (p.item && p.full) s.set('full', '1')
  const qs = s.toString()
  return qs ? `${path}?${qs}` : path
}

/** The library filters as URL params (no view, item or ranking). */
function filterParams(q: Query): URLSearchParams {
  const s = new URLSearchParams()
  if (q.q) s.set('q', q.q)
  if (q.tags.length) s.set('tags', q.tags.join(','))
  if (q.folder) s.set('folder', q.folder)
  if (q.type) s.set('type', q.type)
  if (q.rating) s.set('rating', String(q.rating))
  if (q.person) s.set('person', q.person)
  if (q.flag) s.set('flag', q.flag)
  if (q.label) s.set('label', q.label)
  addM45Params(q, s)
  addProjectParams(q, s)
  return s
}

function tolerance(v: string | null): number {
  const n = Number(v)
  return v !== null && v !== '' && Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : emptyQuery.color_tol
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
    fixity: s.get('fixity') === 'failed' ? 'failed' : null,
    // an unknown type is ignored (the server would refuse it)
    type: isItemKind(s.get('type')) ? s.get('type') : null,
    rating: Math.max(0, Math.min(5, Number(s.get('rating')) || 0)),
    order: s.get('order') || emptyQuery.order,
    created_after: s.get('after') || null,
    created_before: s.get('before') || null,
    deleted: s.get('trash') === '1',
    semantic: s.get('semantic') ?? '',
    similar: s.get('similar') || null,
    color: /^#[0-9a-f]{6}$/i.test(s.get('color') ?? '') ? (s.get('color') as string).toLowerCase() : null,
    color_tol: tolerance(s.get('tol')),
    text: s.get('text') ?? '',
    person: s.get('person') || null,
    image: s.get('image') || null,
    flag: ['pick', 'reject', 'none'].includes(s.get('flag') ?? '') ? s.get('flag') : null,
    label: LABEL_PARAMS.includes(s.get('label') ?? '') ? s.get('label') : null,
    ...m45FromSearch(s),
    ...projectFromSearch(s),
  }
  let smartId: string | null = null
  const [head, arg] = parts
  if (!head || head === 'unfiled') query.view = 'unfiled'
  else if (head === 'trash') query.deleted = true
  else if (head === 'folder' && arg) query.folder = arg
  else if (head === 'tag' && arg) query.tags = [arg, ...query.tags.filter((t) => t !== arg)]
  else if (head === 'smart' && arg) smartId = arg
  else if (head === 'settings') {
    // the library behind Settings is All items (closing Settings shows it)
    const section = SETTINGS_SECTIONS.includes(arg as SettingsSection) ? (arg as SettingsSection) : 'tokens'
    return { query: { ...emptyQuery }, smartId: null, item: null, full: false, settings: section }
  } else if (head === 'organise') {
    const section = ORGANISE_SECTIONS.includes(arg as OrganiseSection) ? (arg as OrganiseSection) : 'duplicates'
    return { query: { ...emptyQuery }, smartId: null, item: null, full: false, organise: section }
  }
  else if (head === 'timeline' || head === 'map') {
    const g = s.get('group')
    const screen: Screen =
      head === 'map' ? { kind: 'map' } : { kind: 'timeline', group: g === 'day' || g === 'year' ? g : 'month' }
    const filters: Query = {
      ...emptyQuery,
      q: query.q,
      tags: query.tags,
      folder: query.folder,
      type: query.type,
      rating: query.rating,
      person: query.person,
      flag: query.flag,
      label: query.label,
      ...m45FromSearch(s),
      ...projectFromSearch(s),
    }
    return { query: filters, smartId: null, item: null, full: false, screen }
  } else if (head === 'add') {
    return { query: { ...emptyQuery }, smartId: null, item: null, full: false, screen: { kind: 'add', search: search && search !== '?' ? (search.startsWith('?') ? search : `?${search}`) : '' } }
  }
  else if (head === 'projects' || (head === 'project' && arg)) {
    const screen: Screen = head === 'project' ? { kind: 'project', id: arg } : { kind: 'projects', ...(s.get('archived') === '1' ? { archived: true } : {}) }
    return { query: { ...emptyQuery }, smartId: null, item: null, full: false, screen }
  }
  else if (head === 'boards' || (head === 'board' && arg) || head === 'triage') {
    const screen: Screen =
      head === 'boards'
        ? { kind: 'boards', ...(s.get('trash') === '1' ? { trash: true } : {}) }
        : head === 'board'
          ? { kind: 'board', id: arg }
          : { kind: 'triage', view: s.get('view') === 'all' ? 'all' : 'unfiled', folder: s.get('folder') || null }
    return { query: { ...emptyQuery }, smartId: null, item: null, full: false, screen }
  }
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
  // moving between Settings sections doesn't stack up history: Back (or
  // Close) leaves Settings in one step
  if (cur.settings && next.settings) return 'replace'
  if (cur.organise && next.organise) return 'replace'
  // a search started from an item, a colour, an image or a person is a
  // new place even in the same view, so Back returns to where you were
  const pivot = (q: Query) => [q.similar, q.color, q.image, q.person].join('|')
  if (pivot(next.query) !== pivot(cur.query) && pivot(next.query) !== '|||') return opts.curIsMenu ? 'replace' : 'push'
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
