// Viewing aids (F5): greyscale / value check, 3- and 5-step posterise,
// mirror, grid overlay, repeat-tile preview and true 100 %. They change
// only what is drawn on screen: nothing is saved and no request is made.
// No React here (tests/viewAids.test.ts); the components live in
// components/viewaids/ViewAids.tsx, and the state is one per tab
// (viewAidsStore), so a value check stays on from the preview into
// compare and Present until it's switched off or the page reloads.

export type GridKind = 'off' | 'thirds' | '4x4' | 'custom'
export type TileMode = 'straight' | 'half-drop' | 'half-brick'

export interface ViewAids {
  grey: boolean
  /** 0 = off; else the number of values (posterise implies greyscale) */
  posterize: 0 | 3 | 5
  mirror: boolean
  grid: GridKind
  /** lines per side for the custom grid (2-24) */
  gridN: number
  /** 0 = off, 2 = 2×2, 3 = 3×3 */
  tile: 0 | 2 | 3
  tileMode: TileMode
  /** 1 image pixel = 1 device pixel */
  actual: boolean
}

export const DEFAULT_AIDS: ViewAids = {
  grey: false,
  posterize: 0,
  mirror: false,
  grid: 'off',
  gridN: 6,
  tile: 0,
  tileMode: 'straight',
  actual: false,
}

export const GRID_N_MIN = 2
export const GRID_N_MAX = 24

/** Which aids a surface offers (compare has no tile; its own zoom does 100 %). */
export interface AidFeatures {
  tile: boolean
  actual: boolean
}
export const ALL_FEATURES: AidFeatures = { tile: true, actual: true }

export function clampGridN(n: number): number {
  return Number.isFinite(n) ? Math.min(GRID_N_MAX, Math.max(GRID_N_MIN, Math.round(n))) : DEFAULT_AIDS.gridN
}

const POSTERIZE_CYCLE: ViewAids['posterize'][] = [0, 3, 5]
const GRID_CYCLE: GridKind[] = ['off', 'thirds', '4x4', 'custom']
const TILE_CYCLE: ViewAids['tile'][] = [0, 2, 3]

function next<T>(cycle: T[], cur: T): T {
  return cycle[(cycle.indexOf(cur) + 1) % cycle.length]
}

interface KeyLike {
  key: string
  metaKey: boolean
  ctrlKey: boolean
  altKey: boolean
  shiftKey?: boolean
}

/** The keys (keymap.ts lists them for the preview and compare):
 * G greyscale, V posterise (off → 3 → 5), H mirror, L grid (off → thirds
 * → 4×4 → custom), T repeat tile (off → 2×2 → 3×3), 1 true 100 %.
 * Returns the new aids, or null when the key isn't one of them. */
export function viewAidKey(e: KeyLike, aids: ViewAids, features: AidFeatures = ALL_FEATURES): ViewAids | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key
  if (key === 'g') return { ...aids, grey: !aids.grey, posterize: aids.grey ? 0 : aids.posterize }
  if (key === 'v') return { ...aids, posterize: next(POSTERIZE_CYCLE, aids.posterize) }
  if (key === 'h') return { ...aids, mirror: !aids.mirror }
  if (key === 'l') return { ...aids, grid: next(GRID_CYCLE, aids.grid) }
  if (key === 't' && features.tile) return { ...aids, tile: next(TILE_CYCLE, aids.tile), actual: false }
  if (key === '1' && features.actual) return { ...aids, actual: !aids.actual, tile: 0 }
  return null
}

/** Escape leaves a mode that replaces the picture (100 %, tile) first. */
export function escapeAids(aids: ViewAids): ViewAids | null {
  if (aids.actual || aids.tile) return { ...aids, actual: false, tile: 0 }
  return null
}

export function anyAid(aids: ViewAids): boolean {
  return aids.grey || aids.posterize > 0 || aids.mirror || aids.grid !== 'off' || aids.tile > 0 || aids.actual
}

/** The SVG filter an image needs: 'grey', 'post3', 'post5' or null. */
export function filterKind(aids: ViewAids): 'grey' | 'post3' | 'post5' | null {
  if (aids.posterize === 3) return 'post3'
  if (aids.posterize === 5) return 'post5'
  return aids.grey ? 'grey' : null
}

/** The band values of an n-step posterise (feFuncX type="discrete"): n
 * evenly spaced greys from black to white. */
export function posterizeTable(steps: number): string {
  if (steps < 2) return '0 1'
  return Array.from({ length: steps }, (_, i) => +(i / (steps - 1)).toFixed(4)).join(' ')
}

/** Relative luminance (Rec. 709 / sRGB primaries); the filter applies it in
 * linear light, so the grey has the colour's real lightness. */
export const LUMA_MATRIX = '0.2126 0.7152 0.0722 0 0  0.2126 0.7152 0.0722 0 0  0.2126 0.7152 0.0722 0 0  0 0 0 1 0'

/** Grid lines as fractions of the picture's width/height (shared by both axes). */
export function gridLines(aids: Pick<ViewAids, 'grid' | 'gridN'>): number[] {
  const n = aids.grid === 'thirds' ? 3 : aids.grid === '4x4' ? 4 : aids.grid === 'custom' ? clampGridN(aids.gridN) : 0
  return n ? Array.from({ length: n - 1 }, (_, i) => (i + 1) / n) : []
}

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** Where an object-fit: contain picture sits inside its element's box. */
export function containedRect(boxW: number, boxH: number, natW: number, natH: number): Rect {
  if (!natW || !natH || !boxW || !boxH) return { x: 0, y: 0, w: boxW, h: boxH }
  const s = Math.min(boxW / natW, boxH / natH)
  const w = natW * s
  const h = natH * s
  return { x: (boxW - w) / 2, y: (boxH - h) / 2, w, h }
}

/** The tiles of an n × n repeat, in tile units (the box is n × n units).
 * Half-drop shifts every other column down by half a tile, half-brick every
 * other row across, with an extra tile so no gap shows at the edge. */
export function tileCells(n: number, mode: TileMode): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = []
  for (let c = 0; c < n; c++) {
    const dy = mode === 'half-drop' && c % 2 === 1 ? 0.5 : 0
    for (let r = dy ? -1 : 0; r < n; r++) {
      const dx = mode === 'half-brick' && r % 2 === 1 ? 0.5 : 0
      out.push({ x: c + dx, y: r + dy })
      // a shifted row needs one more tile at its start to fill the edge
      if (dx && c === 0) out.push({ x: dx - 1, y: r })
    }
  }
  return out.sort((a, b) => a.y - b.y || a.x - b.x)
}

/** The size, in CSS pixels, that shows an image 1:1 on the device. `shownW`
 * is the width of the copy actually drawn (the original, or a preview when
 * the browser can't draw the original); its pixels map 1:1. */
export function actualSize(shownW: number, shownH: number, dpr: number): { w: number; h: number } {
  const d = dpr > 0 && Number.isFinite(dpr) ? dpr : 1
  return { w: shownW / d, h: shownH / d }
}

/** The copy drawn at 100 % and how big it is: the original when the browser
 * shows it, else the largest preview (scaled to fit `previewSide`). */
export function actualSource(
  natW: number,
  natH: number,
  browserShowsOriginal: boolean,
  previewSide: number,
): { w: number; h: number; original: boolean } {
  if (browserShowsOriginal || Math.max(natW, natH) <= previewSide) return { w: natW, h: natH, original: true }
  const s = previewSide / Math.max(natW, natH)
  return { w: Math.round(natW * s), h: Math.round(natH * s), original: false }
}

/** The zoom (relative to the fitted size) at which a picture fitted to
 * `fittedCssW` CSS px shows its `natW` pixels 1:1 on the device (compare's
 * 100 % button). */
export function trueZoom(natW: number, fittedCssW: number, dpr: number): number | null {
  if (!natW || !fittedCssW) return null
  const d = dpr > 0 && Number.isFinite(dpr) ? dpr : 1
  return natW / d / fittedCssW
}

/** Short labels for what is on, for the badge over the picture. */
export function aidsSummary(aids: ViewAids, features: AidFeatures = ALL_FEATURES): string[] {
  const out: string[] = []
  if (aids.posterize) out.push(`${aids.posterize} values`)
  else if (aids.grey) out.push('Greyscale')
  if (aids.mirror) out.push('Mirrored')
  if (aids.grid !== 'off') out.push(aids.grid === 'thirds' ? 'Thirds' : aids.grid === '4x4' ? '4 × 4 grid' : `${clampGridN(aids.gridN)} × ${clampGridN(aids.gridN)} grid`)
  if (aids.tile && features.tile) out.push(`Repeat ${aids.tile} × ${aids.tile}${aids.tileMode === 'straight' ? '' : aids.tileMode === 'half-drop' ? ', half-drop' : ', half-brick'}`)
  if (aids.actual && features.actual) out.push('100 %')
  return out
}

// ---------- the one state per tab ----------

type Listener = () => void

export function createAidsStore(initial: ViewAids = DEFAULT_AIDS) {
  let state = initial
  const listeners = new Set<Listener>()
  return {
    get: () => state,
    set(next: ViewAids | ((a: ViewAids) => ViewAids)) {
      const v = typeof next === 'function' ? next(state) : next
      if (v === state) return
      state = { ...v, gridN: clampGridN(v.gridN) }
      for (const l of [...listeners]) l()
    },
    subscribe(l: Listener) {
      listeners.add(l)
      return () => void listeners.delete(l)
    },
    reset() {
      this.set(DEFAULT_AIDS)
    },
  }
}

export const viewAidsStore = createAidsStore()
