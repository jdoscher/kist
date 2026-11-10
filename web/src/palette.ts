// The ⌘K command palette (Milestone 3), no React (tests/palette.test.ts).
// Results come from GET /api/palette (folders, smart folders, tags, boards,
// people, items, and the server's action list) plus actions the web owns;
// local ones are ranked here with the same rule the server uses (exact,
// then prefix, then a word start, then contains, then in order), and the
// last few picks are remembered.

export type PaletteKind = 'folder' | 'smart_folder' | 'tag' | 'board' | 'person' | 'item' | 'action' | 'recent'

export interface PaletteEntry {
  kind: PaletteKind
  id: string
  title: string
  subtitle?: string
  /** extra words to match (an action's synonyms) */
  keywords?: string
}

/** How well `title` (and keywords) match `q`: lower is better; null is no
 * match. Every word of the query has to appear. */
export function matchScore(q: string, title: string, keywords = ''): number | null {
  const needle = q.trim().toLowerCase()
  if (!needle) return 3
  const t = title.toLowerCase()
  if (t === needle) return 0
  if (t.startsWith(needle)) return 1
  const hay = `${t} ${keywords.toLowerCase()}`
  if (new RegExp(`(^|[\\s/_\\-.:#])${escapeRe(needle)}`).test(hay)) return 2
  if (hay.includes(needle)) return 3
  const words = needle.split(/\s+/).filter(Boolean)
  if (words.length > 1 && words.every((w) => hay.includes(w))) return 4
  // letters in order (fuzzy, "nbrd" -> "New board"), only for short titles
  if (needle.length >= 2 && subsequence(needle.replace(/\s+/g, ''), t)) return 5
  return null
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function subsequence(needle: string, hay: string): boolean {
  let i = 0
  for (const ch of hay) if (ch === needle[i]) i++
  return i === needle.length
}

/** Local entries that match, best first (ties keep their order). */
export function rankLocal(q: string, entries: PaletteEntry[]): PaletteEntry[] {
  return entries
    .map((e, i) => ({ e, i, s: matchScore(q, e.title, e.keywords) }))
    .filter((x): x is { e: PaletteEntry; i: number; s: number } => x.s !== null)
    .sort((a, b) => a.s - b.s || a.i - b.i)
    .map((x) => x.e)
}

export const KIND_ORDER: PaletteKind[] = ['recent', 'action', 'folder', 'smart_folder', 'board', 'tag', 'person', 'item']

export const KIND_LABEL: Record<PaletteKind, string> = {
  recent: 'Recent',
  action: 'Actions',
  folder: 'Folders',
  smart_folder: 'Smart folders',
  board: 'Boards',
  tag: 'Tags',
  person: 'People',
  item: 'Items',
}

/** One list for the palette: web actions first (they act on what you're
 * doing), then the server's results by kind; actions with the same id
 * (the server's and ours) show once. With no query, recents lead. */
export function mergeResults(
  q: string,
  local: PaletteEntry[],
  server: PaletteEntry[],
  recent: PaletteEntry[] = [],
): { kind: PaletteKind; entries: PaletteEntry[] }[] {
  const actions = rankLocal(q, local)
  const seen = new Set(actions.map((a) => `action:${a.id}`))
  const groups = new Map<PaletteKind, PaletteEntry[]>()
  if (!q.trim() && recent.length) groups.set('recent', recent.map((r) => ({ ...r })))
  groups.set('action', actions)
  for (const e of server) {
    const key = `${e.kind}:${e.id}`
    if (seen.has(key)) continue
    if (e.kind === 'action' && q.trim() && matchScore(q, e.title, e.subtitle) === null) continue
    seen.add(key)
    const list = groups.get(e.kind) ?? []
    list.push(e)
    groups.set(e.kind, list)
  }
  return KIND_ORDER.filter((k) => groups.get(k)?.length).map((k) => ({ kind: k, entries: groups.get(k) as PaletteEntry[] }))
}

// ---------- recents ----------

const RECENT_KEY = 'kist.paletteRecent'
export const RECENT_MAX = 6

type Store = Pick<Storage, 'getItem' | 'setItem'>

function store(): Store | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function readRecent(s: Store | null = store()): PaletteEntry[] {
  try {
    const raw = JSON.parse(s?.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(raw)
      ? raw.filter((e) => e && typeof e.id === 'string' && typeof e.title === 'string' && typeof e.kind === 'string').slice(0, RECENT_MAX)
      : []
  } catch {
    return []
  }
}

/** Remember a pick (newest first, no duplicates). Items aren't kept: they
 * are a search, not a place. */
export function pushRecent(entry: PaletteEntry, s: Store | null = store()): PaletteEntry[] {
  const list = readRecent(s).filter((e) => !(e.kind === entry.kind && e.id === entry.id))
  const next = [{ kind: entry.kind, id: entry.id, title: entry.title, subtitle: entry.subtitle ?? '' }, ...list].slice(0, RECENT_MAX)
  try {
    s?.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    /* private mode */
  }
  return next
}

/** Wrap-around move through a flat list of `n` options. */
export function moveActive(i: number, delta: number, n: number): number {
  if (n <= 0) return -1
  return (((i + delta) % n) + n) % n
}
