// Present mode (Kist 2.0 F2): the pure half. Types the server sends, the
// show's options, captions, navigation, compare joins, preloading, the
// timers, the offline plan and the messages between the show window, the
// presenter window and the phone. No React, no DOM (tests/present.test.ts).

export type Surround = 'grey' | 'black'
export type PairMode = 'none' | 'stacks' | 'versions'

export interface ShowOptions {
  surround: Surround
  /** off by default; never applied to drawings, documents or compare slides */
  ken_burns: boolean
  /** title and credit under the image */
  captions: boolean
  /** file names on screen (off: IMG_4482_final_FINAL.png stays private) */
  filenames: boolean
  /** timed advance, seconds per slide (0 = off) */
  advance_seconds: number
  /** gesture-drawing timer: 30 s, 1, 2 or 5 min per slide (null = off) */
  gesture_seconds: number | null
  pairs: PairMode
}

export const DEFAULT_OPTIONS: ShowOptions = {
  surround: 'grey',
  ken_burns: false,
  captions: true,
  filenames: false,
  advance_seconds: 0,
  gesture_seconds: null,
  pairs: 'none',
}

export const ADVANCE_CHOICES = [0, 5, 10, 20, 30, 60] as const
export const GESTURE_CHOICES = [30, 60, 120, 300] as const

export interface SlideItem {
  id: string
  filename: string
  ext: string
  mime: string
  kind: string
  width: number | null
  height: number | null
  size_bytes: number
  preview_rev: number
  /** the image (or video) Present loads: the original when the browser
   * colour-manages it, else the full-res display copy */
  src: string | null
  src_kind: 'original' | 'display' | 'screen' | 'none'
  thumb: string | null
  title: string
  credit: string
  license: string
  notes: string
  caption: string
  flag: string | null
  rating: number
  stack_id: string | null
  /** may Ken Burns move it? never drawings, documents, PDFs or video */
  motion_ok: boolean
  low_res: boolean
  /** F7: a cut-out (its thumb keeps the transparency) */
  cutout?: boolean
}

export interface Slide {
  kind: 'single' | 'compare'
  items: SlideItem[]
}

export interface ShowSource {
  kind: 'folder' | 'smart' | 'board' | 'items' | 'search'
  id?: string
  item_ids?: string[]
}

export interface Resolved {
  name: string
  source: ShowSource
  slides: Slide[]
  item_count: number
  truncated: boolean
  low_res: number
  offline_bytes: number
  show_id?: string
  options?: ShowOptions
}

export interface SavedShow {
  id: string
  name: string
  source: ShowSource
  options: ShowOptions
  compare: [string, string][]
  created_at: string
  modified_at: string
}

/** What a Present button hands the setup dialog: how to play it (the
 * slides request) and how to save it (a live folder, smart folder or board,
 * or the items a search found). */
export interface PresentRequest {
  name: string
  source: ShowSource
  /** the grid's query string, for a search source */
  params?: string
  /** the saved source, when the view is a plain folder or smart folder */
  saveAs?: ShowSource
  /** a saved show to open */
  showId?: string
}

// ---------- options ----------

const PREFS_KEY = 'kist.present'

/** The options a new show starts with: the last ones used, except Ken
 * Burns, which is per show and always starts off. */
export function initialOptions(stored: string | null): ShowOptions {
  let saved: Partial<ShowOptions> = {}
  try {
    saved = stored ? (JSON.parse(stored) as Partial<ShowOptions>) : {}
  } catch {
    saved = {}
  }
  const out = { ...DEFAULT_OPTIONS }
  if (saved.surround === 'grey' || saved.surround === 'black') out.surround = saved.surround
  if (typeof saved.captions === 'boolean') out.captions = saved.captions
  if (typeof saved.filenames === 'boolean') out.filenames = saved.filenames
  if (typeof saved.advance_seconds === 'number' && (ADVANCE_CHOICES as readonly number[]).includes(saved.advance_seconds))
    out.advance_seconds = saved.advance_seconds
  if (saved.gesture_seconds === null || (GESTURE_CHOICES as readonly number[]).includes(saved.gesture_seconds as number))
    out.gesture_seconds = saved.gesture_seconds ?? null
  if (saved.pairs === 'none' || saved.pairs === 'stacks' || saved.pairs === 'versions') out.pairs = saved.pairs
  return out
}

export function storedOptions(o: ShowOptions): string {
  const { ken_burns: _kb, ...rest } = o
  void _kb
  return JSON.stringify(rest)
}

export const PRESENT_PREFS_KEY = PREFS_KEY

// ---------- captions ----------

export interface CaptionText {
  title: string
  /** the credit, with the licence after a middle dot */
  credit: string
  filename: string
}

/** What the caption strip shows: title and credit when captions are on,
 * the filename only when filenames are on. Nothing at all: null. */
export function captionFor(it: SlideItem, o: Pick<ShowOptions, 'captions' | 'filenames'>): CaptionText | null {
  const title = o.captions ? it.title.trim() : ''
  const credit = o.captions ? [it.credit.trim(), it.license.trim()].filter(Boolean).join(' · ') : ''
  const filename = o.filenames ? it.filename : ''
  if (!title && !credit && !filename) return null
  return { title, credit, filename }
}

/** Presenter notes: the item's notes, then its caption. */
export function notesFor(slide: Slide | undefined): string {
  if (!slide) return ''
  return slide.items
    .map((it, i) => {
      const text = it.notes.trim() || it.caption.trim()
      if (!text) return ''
      return slide.items.length > 1 ? `${i === 0 ? 'Left' : 'Right'}: ${text}` : text
    })
    .filter(Boolean)
    .join('\n\n')
}

// ---------- navigation ----------

export function clampIndex(i: number, count: number): number {
  if (count <= 0) return 0
  return Math.max(0, Math.min(count - 1, Math.trunc(i)))
}

/** The URLs to warm up around `index`: the next two slides, then the one
 * before (fast advance both ways). */
export function preloadUrls(slides: Slide[], index: number): string[] {
  const out: string[] = []
  for (const i of [index + 1, index + 2, index - 1]) {
    const s = slides[i]
    if (!s) continue
    for (const it of s.items) if (it.src && it.kind !== 'video' && !out.includes(it.src)) out.push(it.src)
  }
  return out
}

/** Put slide i+1's items beside slide i's (a compare slide is two). */
export function joinWithNext(slides: Slide[], i: number): Slide[] {
  const a = slides[i]
  const b = slides[i + 1]
  if (!a || !b || a.kind === 'compare' || b.kind === 'compare') return slides
  const joined: Slide = { kind: 'compare', items: [a.items[0], b.items[0]] }
  return [...slides.slice(0, i), joined, ...slides.slice(i + 2)]
}

/** A compare slide back into its two slides. */
export function splitCompare(slides: Slide[], i: number): Slide[] {
  const s = slides[i]
  if (!s || s.kind !== 'compare') return slides
  const parts: Slide[] = s.items.map((it) => ({ kind: 'single', items: [it] }))
  return [...slides.slice(0, i), ...parts, ...slides.slice(i + 1)]
}

/** The compare pairs a slide list holds, to save with the show. */
export function comparePairs(slides: Slide[]): [string, string][] {
  return slides.filter((s) => s.kind === 'compare' && s.items.length === 2).map((s) => [s.items[0].id, s.items[1].id])
}

/** Every item id, in show order (for the phone remote's scope). */
export function showItemIds(slides: Slide[]): string[] {
  const out: string[] = []
  for (const s of slides) for (const it of s.items) if (!out.includes(it.id)) out.push(it.id)
  return out
}

/** Replace one item everywhere it appears (after a pick or a note). */
export function updateItem(slides: Slide[], id: string, patch: Partial<SlideItem>): Slide[] {
  return slides.map((s) => (s.items.some((it) => it.id === id) ? { ...s, items: s.items.map((it) => (it.id === id ? { ...it, ...patch } : it)) } : s))
}

/** P on a picked item clears the pick (and X a reject): one key, a way out. */
export function nextFlag(current: string | null, pressed: 'pick' | 'reject'): 'pick' | 'reject' | null {
  return current === pressed ? null : pressed
}

/** Ken Burns for this slide: only when the show asks for it, on a single
 * still image that isn't a drawing or a document. */
export function kenBurnsAllowed(slide: Slide | undefined, o: Pick<ShowOptions, 'ken_burns'>): boolean {
  return !!slide && o.ken_burns && slide.kind === 'single' && slide.items[0].motion_ok && slide.items[0].kind === 'image'
}

/** Seconds this slide stays up before moving on by itself (0 = it waits). */
export function slideSeconds(o: Pick<ShowOptions, 'advance_seconds' | 'gesture_seconds'>): number {
  return o.gesture_seconds ?? o.advance_seconds ?? 0
}

/** "0:30", "4:05", "1:02:09". */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const ss = String(s).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

export function gestureLabel(seconds: number): string {
  return seconds < 60 ? `${seconds} s` : `${seconds / 60} min`
}

/** A number typed then Enter jumps there ("slide 14"): digits so far. */
export function typedJump(buffer: string, key: string): { buffer: string; go: number | null } {
  if (/^[0-9]$/.test(key)) return { buffer: (buffer + key).slice(-4), go: null }
  if (key === 'Enter' && buffer) return { buffer: '', go: Number(buffer) - 1 }
  return { buffer: '', go: null }
}

// ---------- offline ----------

export const OFFLINE_CAP_CHOICES = [250, 1000, 4000] as const // MB

export interface OfflinePlan {
  urls: string[]
  /** slides whose images all fit under the cap */
  slides: number
  bytes: number
}

/** The images to keep for offline use, in show order, until the cap. The
 * size of a display copy isn't known before it loads: count 2 MB. */
export function offlinePlan(slides: Slide[], capBytes: number): OfflinePlan {
  const urls: string[] = []
  let bytes = 0
  let kept = 0
  for (const s of slides) {
    const need = s.items.filter((it) => it.src && it.kind !== 'video' && !urls.includes(it.src))
    const size = need.reduce((n, it) => n + (it.src_kind === 'original' ? it.size_bytes : 2_000_000), 0)
    if (bytes + size > capBytes) break
    for (const it of need) urls.push(it.src as string)
    bytes += size
    kept++
  }
  return { urls, slides: kept, bytes }
}

export function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`
  return `${Math.max(1, Math.round(n / 1e3))} KB`
}

// ---------- presenter window and phone ----------

export interface PresentState {
  name: string
  index: number
  count: number
  slide: Slide | null
  next: Slide | null
  notes: string
  startedAt: number
  /** when this slide's timer runs out (timed advance / gesture), or null */
  slideEndsAt: number | null
  paused: boolean
  blank: boolean
  options: ShowOptions
}

export type PresenterMessage =
  | { type: 'state'; state: PresentState }
  | { type: 'cmd'; cmd: RemoteCommand; index?: number }
  | { type: 'hello' }
  | { type: 'bye' }

export type RemoteCommand = 'next' | 'prev' | 'pick' | 'reject' | 'first' | 'last' | 'goto' | 'blank'

export function channelName(showKey: string): string {
  return `kist-present-${showKey}`
}

export function presenterUrl(showKey: string): string {
  return `/present/presenter?ch=${encodeURIComponent(showKey)}`
}

/** The phone's address: the token rides in the fragment, so it never
 * reaches a server or proxy log. */
export function remoteUrl(origin: string, path: string): string {
  return origin.replace(/\/$/, '') + path
}

/** /present/remote/<id>#<token> -> its parts. */
export function parseRemoteLocation(pathname: string, hash: string): { id: string; token: string } | null {
  const m = /^\/present\/remote\/([0-9A-HJKMNP-TV-Z]{26})\/?$/.exec(pathname)
  const token = hash.replace(/^#/, '')
  if (!m || !/^[A-Za-z0-9_-]{20,100}$/.test(token)) return null
  return { id: m[1], token }
}

/** The state the show window publishes to a paired phone (small, no URLs:
 * the phone fetches thumbnails through its own relay). */
export function phoneState(s: PresentState) {
  const it = s.slide?.items[0]
  return {
    index: s.index,
    count: s.count,
    item_id: it?.id ?? null,
    next_item_id: s.next?.items[0]?.id ?? null,
    title: (s.slide?.items ?? []).map((x) => x.title).filter(Boolean).join(' / '),
    credit: (s.slide?.items ?? []).map((x) => x.credit).filter(Boolean).join(' / '),
    notes: s.notes,
    flag: (s.slide?.items ?? []).map((x) => x.flag ?? '-').join(','),
    compare: s.slide?.kind === 'compare',
    blank: s.blank,
  }
}
