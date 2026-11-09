// Grid layout: packs items into rows of tiles for the virtualized grid.
// Pure functions (no React) so the packing can be tested on its own.

export type GridLayout = 'justified' | 'square'

/** Space each tile takes besides its own box: 4 px margin on both sides. */
const TILE_GAP = 8

/** Shapes beyond these are cropped, so very tall or wide images stay readable. */
const MIN_ASPECT = 0.5 // 1:2
const MAX_ASPECT = 3 // 3:1

/** How far a justified row may stretch or shrink from the target height. */
const FLEX = 0.15

export const TILE_SIZE_MIN = 120
export const TILE_SIZE_MAX = 400
export const TILE_SIZE_DEFAULT = 200

export interface Sized {
  width: number | null
  height: number | null
  has_thumb: boolean
}

interface RowTile {
  index: number // position in the flat item list
  width: number // tile box width (px), margin excluded
}

export interface Row {
  tiles: RowTile[]
  height: number // tile box height (px), margin excluded
}

/** The shape a tile shows: the image's own ratio, clamped; square if unknown. */
export function tileAspect(item: Sized): number {
  if (!item.has_thumb || !item.width || !item.height) return 1
  const r = item.width / item.height
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, r))
}

function squareRows(count: number, avail: number, size: number): Row[] {
  // same maths as the original fixed grid: size 200 -> 210 px cells
  const cols = Math.max(2, Math.floor(avail / (size + 10)))
  const tile = Math.floor(avail / cols) - TILE_GAP
  const rows: Row[] = []
  for (let start = 0; start < count; start += cols) {
    const tiles: RowTile[] = []
    for (let i = start; i < Math.min(start + cols, count); i++) tiles.push({ index: i, width: tile })
    rows.push({ tiles, height: tile })
  }
  return rows
}

/** Widths that fill `space` exactly, proportional to the aspects. */
function fillWidths(aspects: number[], space: number): number[] {
  const sum = aspects.reduce((a, b) => a + b, 0)
  const widths = aspects.map((r) => Math.max(1, Math.floor((r / sum) * space)))
  let rest = space - widths.reduce((a, b) => a + b, 0)
  for (let i = 0; rest > 0; i = (i + 1) % widths.length, rest--) widths[i]++
  return widths
}

function justifiedRows(items: Sized[], avail: number, target: number): Row[] {
  const rows: Row[] = []
  const lo = target * (1 - FLEX)
  const hi = target * (1 + FLEX)
  let start = 0
  while (start < items.length) {
    const aspects: number[] = []
    let sum = 0
    let complete = false
    for (let i = start; i < items.length; i++) {
      const r = tileAspect(items[i])
      const fitWith = (avail - TILE_GAP * (aspects.length + 1)) / (sum + r)
      if (fitWith <= target) {
        // adding this tile fills the row; keep it if that lands closer to the
        // target height than ending the row without it
        const fitWithout =
          aspects.length > 0 ? (avail - TILE_GAP * aspects.length) / sum : Number.POSITIVE_INFINITY
        if (Math.abs(fitWith - target) <= Math.abs(fitWithout - target)) {
          aspects.push(r)
          sum += r
        }
        complete = true
        break
      }
      aspects.push(r)
      sum += r
    }
    const space = avail - TILE_GAP * aspects.length
    let height: number
    let widths: number[]
    if (complete) {
      height = Math.round(Math.min(hi, Math.max(lo, space / sum)))
      widths = fillWidths(aspects, space) // beyond the flex range, tiles crop
    } else {
      // last, partial row keeps the normal height and isn't stretched
      height = target
      widths = aspects.map((r) => Math.max(1, Math.round(r * target)))
    }
    rows.push({
      tiles: widths.map((w, k) => ({ index: start + k, width: w })),
      height,
    })
    start += aspects.length
  }
  return rows
}

/**
 * Pack items into rows. `avail` is the usable content width; `size` is the
 * target tile height (justified) or tile edge (square).
 */
export function layoutRows(items: Sized[], avail: number, size: number, mode: GridLayout): Row[] {
  if (items.length === 0 || avail <= 0) return []
  return mode === 'square'
    ? squareRows(items.length, avail, size)
    : justifiedRows(items, avail, size)
}

/** Which thumbnail covers a tile best: thumb_512 (short side 512, long side
 * at most 2048) or thumb_1024 (long side 1024). Prefers the smaller file. */
export function pickThumbSize(
  item: Sized,
  tileW: number,
  tileH: number,
  dpr: number,
): 512 | 1024 {
  const pw = tileW * dpr
  const ph = tileH * dpr
  if (!item.width || !item.height) return Math.max(pw, ph) <= 512 ? 512 : 1024
  const w = item.width
  const h = item.height
  const s512 = Math.min(1, 512 / Math.min(w, h), 2048 / Math.max(w, h))
  const s1024 = Math.min(1, 1024 / Math.max(w, h))
  const cover = (s: number) => Math.min((w * s) / pw, (h * s) / ph)
  const c512 = cover(s512)
  if (c512 >= 1) return 512
  return cover(s1024) > c512 ? 1024 : 512
}

/**
 * Tile size that gives `cols` columns across `avail` px: the square layout's
 * edge, or the justified layout's target row height. For justified rows pass
 * the items' typical aspect (see typicalAspect) so a row holds about `cols`
 * of them whether the folder is mostly portrait or landscape.
 */
export function mobileTileSize(avail: number, cols: number, aspect = 1): number {
  return Math.max(40, Math.floor((avail / cols - TILE_GAP - 2) / aspect))
}

/** Median tile aspect of the items (1 when there are none). */
export function typicalAspect(items: Sized[]): number {
  if (items.length === 0) return 1
  const a = items.map(tileAspect).sort((x, y) => x - y)
  return a[Math.floor(a.length / 2)]
}

// ---- arrow-key navigation over the packed rows (Grid owns only the listener)

export interface TilePos {
  row: number
  center: number // horizontal centre of the tile box (px, from the row's left)
}

/** Item index -> its row and horizontal centre, from layoutRows' output. */
export function tilePositions(rows: Row[]): TilePos[] {
  const pos: TilePos[] = []
  rows.forEach((row, r) => {
    let x = 0
    for (const t of row.tiles) {
      pos[t.index] = { row: r, center: x + TILE_GAP / 2 + t.width / 2 }
      x += t.width + TILE_GAP
    }
  })
  return pos
}

export type NavKey = 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown'

export function isNavKey(key: string): key is NavKey {
  return key === 'ArrowLeft' || key === 'ArrowRight' || key === 'ArrowUp' || key === 'ArrowDown'
}

/**
 * The item an arrow key moves to from `idx` (-1 when nothing is focused):
 * left/right step through the list, up/down pick the tile in the row
 * above/below whose centre is nearest, and stay put at the first/last row.
 * Returns -1 only when there are no items.
 */
export function nextIndex(
  key: NavKey,
  idx: number,
  count: number,
  rows: Row[],
  positions: TilePos[] = tilePositions(rows),
): number {
  if (count <= 0) return -1
  let next: number
  if (key === 'ArrowRight') next = Math.min(idx + 1, count - 1)
  else if (key === 'ArrowLeft') next = Math.max(idx - 1, 0)
  else {
    const here = positions[idx]
    if (!here) return 0
    const target = rows[here.row + (key === 'ArrowDown' ? 1 : -1)]
    if (!target || target.tiles.length === 0) return idx
    next = target.tiles[0].index
    for (const t of target.tiles) {
      if (Math.abs(positions[t.index].center - here.center) < Math.abs(positions[next].center - here.center))
        next = t.index
    }
  }
  return next < 0 ? 0 : next
}
