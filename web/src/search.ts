// The search box's modes and what each sends (no React; tests/search.test.ts).
//
// One box, with a mode menu beside it:
//   Auto (default)  words that look like a file name, a code or a quoted
//                   phrase search the words (q); anything else is treated as
//                   a description and runs words + meaning together (q +
//                   semantic: the server blends both rankings), so "orvis"
//                   finds the receipt that says it and "dog on a beach"
//                   finds the photo that shows it. "#2f4f3a" searches
//                   that colour.
//   Words           q only: file names, notes, the text in images, captions
//   Meaning         semantic only: what the picture shows
//   Text in images  text only: words inside screenshots and PDFs
//   Colour          a colour picker (color + color_tol)
//   Image           drop, paste or pick an image (POST /api/search/image)
// "More like this" (similar) and a person come from items, not the box.

import { emptyQuery, type Match, type Query } from './api.ts'

export type SearchMode = 'auto' | 'words' | 'meaning' | 'text' | 'color' | 'image'

export const SEARCH_MODES: { id: SearchMode; label: string; hint: string }[] = [
  { id: 'auto', label: 'Auto', hint: 'Words and meaning together; names and codes as words' },
  { id: 'words', label: 'Words', hint: 'File names, notes, captions and the text in images' },
  { id: 'meaning', label: 'Meaning', hint: 'Describe what the picture shows' },
  { id: 'text', label: 'Text in images', hint: 'Only words inside screenshots and PDFs' },
  { id: 'color', label: 'Colour', hint: 'Pick a colour' },
  { id: 'image', label: 'Image', hint: 'Drop, paste or choose a picture' },
]

export const DEFAULT_COLOR_TOL = 30

const HEX = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i

/** "#abc" / "#aabbcc" (with the #) as a normalised "#aabbcc", else null. */
export function hexColor(text: string, needHash = true): string | null {
  const t = text.trim()
  if (needHash && !t.startsWith('#')) return null
  const m = HEX.exec(t)
  if (!m) return null
  const h = m[1].toLowerCase()
  return `#${h.length === 3 ? h.split('').map((c) => c + c).join('') : h}`
}

/**
 * Whether Auto should search these words literally (keyword only) instead
 * of also by meaning: a quoted phrase, a file name or extension, a path or
 * address, an identifier mixing letters and digits (IMG_2231, DSC0042, v2),
 * a plain number.
 */
export function looksLikeKeyword(text: string): boolean {
  const t = text.trim()
  if (!t) return true
  if (/^".*"$/.test(t) || /^'.*'$/.test(t)) return true
  if (/[\\/_@]/.test(t)) return true
  if (/\.[a-z0-9]{2,5}$/i.test(t) || /\w\.\w/.test(t)) return true
  return t.split(/\s+/).some((w) => /^\d+$/.test(w) || (/\d/.test(w) && /[a-z]/i.test(w)))
}

/** The fields the search box owns: cleared whenever it searches again. */
const BOX_FIELDS = { q: '', semantic: '', text: '', color: null, similar: null, image: null } satisfies Partial<Query>

/** What the box sends for `text` in `mode` (a query patch). */
export function buildSearch(text: string, mode: SearchMode): Partial<Query> {
  const t = text.trim()
  if (!t) return { ...BOX_FIELDS }
  switch (mode) {
    case 'words':
      return { ...BOX_FIELDS, q: t }
    case 'meaning':
      return { ...BOX_FIELDS, semantic: t }
    case 'text':
      return { ...BOX_FIELDS, text: t }
    case 'color': {
      const hex = hexColor(t, false)
      return hex ? { ...BOX_FIELDS, color: hex } : { ...BOX_FIELDS, q: t }
    }
    case 'image': // the image comes from a drop/paste/picker; typed words narrow it
      return { ...BOX_FIELDS, q: t }
    default: {
      const hex = hexColor(t)
      if (hex) return { ...BOX_FIELDS, color: hex }
      if (looksLikeKeyword(t)) return { ...BOX_FIELDS, q: t.replace(/^(["'])(.*)\1$/, '$2') }
      return { ...BOX_FIELDS, q: t, semantic: t }
    }
  }
}

/** The text the box shows for a query. */
export function searchText(q: Pick<Query, 'q' | 'semantic' | 'text'>): string {
  return q.q || q.semantic || q.text || ''
}

/** Which mode a query reads as (e.g. after Back, or opening a link). The
 * preferred mode wins when the query is what it would have sent. */
export function modeOf(q: Query, preferred: SearchMode = 'auto'): SearchMode {
  if (q.image) return 'image'
  if (q.color && !q.q && !q.semantic && !q.text) return preferred === 'auto' ? 'auto' : 'color'
  if (q.text) return 'text'
  if (q.semantic && q.q === q.semantic) return 'auto'
  if (q.semantic) return 'meaning'
  if (q.q) return preferred === 'auto' && looksLikeKeyword(q.q) ? 'auto' : 'words'
  return preferred
}

/** Ranked by relevance rather than the chosen sort order. */
export function isRanked(q: Pick<Query, 'semantic' | 'similar' | 'color' | 'text' | 'image'>): boolean {
  return !!(q.semantic || q.similar || q.color || q.text || q.image)
}

/** Every search the box or an item started is cleared (the view stays). */
export function clearSearch(): Partial<Query> {
  return { ...BOX_FIELDS }
}

/** "More like this": over the whole library, ranked by look. */
export function moreLikeThis(id: string, current: Query): Query {
  return { ...emptyQuery, order: current.order, similar: id }
}

/** A colour search over the whole library. */
export function colourSearch(hex: string, current: Query, tol = current.color_tol || DEFAULT_COLOR_TOL): Query {
  return { ...emptyQuery, order: current.order, color: hexColor(hex, false) ?? hex, color_tol: tol }
}

export const MATCH_LABEL: Record<Match['kind'], string> = {
  keyword: 'Words',
  meaning: 'Meaning',
  text: 'Text in image',
  color: 'Colour',
  similar: 'Looks alike',
  hybrid: 'Words + meaning',
}

/** The match kind in words, for this search: "hybrid" means the words
 * matched as well as the meaning, the look or the image. */
export function matchLabel(kind: Match['kind'], q?: Pick<Query, 'semantic' | 'similar' | 'image'>): string {
  if (kind === 'hybrid' && q && !q.semantic && (q.similar || q.image)) return 'Words + look'
  return MATCH_LABEL[kind] ?? kind
}

export interface SnippetPart {
  text: string
  hit: boolean
}

/** "…the <<zero trust>> slide…" -> parts with the marked words as hits.
 * An unclosed << marks the rest; stray >> are kept as text. */
export function parseSnippet(snippet: string | null | undefined): SnippetPart[] {
  if (!snippet) return []
  const out: SnippetPart[] = []
  let rest = snippet
  while (rest) {
    const open = rest.indexOf('<<')
    if (open < 0) {
      out.push({ text: rest, hit: false })
      break
    }
    if (open > 0) out.push({ text: rest.slice(0, open), hit: false })
    const close = rest.indexOf('>>', open + 2)
    const end = close < 0 ? rest.length : close
    const hit = rest.slice(open + 2, end)
    if (hit) out.push({ text: hit, hit: true })
    rest = close < 0 ? '' : rest.slice(close + 2)
  }
  // merge neighbours of the same kind
  return out.reduce<SnippetPart[]>((acc, p) => {
    const last = acc.at(-1)
    if (last && last.hit === p.hit) last.text += p.text
    else acc.push({ ...p })
    return acc
  }, [])
}

/** The snippet as plain text (for labels and titles). */
export function snippetText(snippet: string | null | undefined): string {
  return parseSnippet(snippet).map((p) => p.text).join('')
}

/** What a ranked search is called in headings: "Like “boots.jpg”". */
export function searchTitle(q: Query, names: { item?: (id: string) => string | undefined; person?: (id: string) => string | undefined } = {}): string | null {
  if (q.similar) {
    const n = names.item?.(q.similar)
    return n ? `More like “${n}”` : 'More like this'
  }
  if (q.image) return 'Like your image'
  if (q.color) return `Colour ${q.color}`
  if (q.person) return names.person?.(q.person) ?? 'Person'
  return null
}

/** Whether a tile's match kind is worth a badge: only where results
 * matched in different ways (words + meaning), and only for the ones the
 * words found; a badge saying "Meaning" on every tile says nothing. */
export function showMatchBadge(kind: string | undefined, q: Pick<Query, 'q' | 'semantic' | 'similar' | 'image'>): boolean {
  if (!kind || !q.q || !(q.semantic || q.similar || q.image)) return false
  return kind === 'hybrid' || kind === 'keyword'
}


/** A ranked search the server couldn't run (the AI service is down: 503;
 * an image from before a reload: 410): say why, offer the words instead. */
export function rankedErrorText(status: number | undefined, q: Query): string | null {
  if (status === 410 && q.image) return 'The image you searched with isn’t kept after a reload. Drop or paste it on the search box again.'
  if (status === 503 && isRanked(q))
    return 'Searching by meaning, look or image isn’t available right now: the AI service isn’t answering.'
  return null
}

