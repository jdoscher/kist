// Grid layout: packs items into rows of tiles for the virtualized grid.
// Pure functions (no React) so the packing can be tested on its own.

/** 'justified' = Rows (the default), 'square', 'columns' = masonry. */
export type GridLayout = 'justified' | 'square' | 'columns'

export const GRID_LAYOUTS: readonly GridLayout[] = ['justified', 'square', 'columns']

export function isGridLayout(v: unknown): v is GridLayout {
  return typeof v === 'string' && (GRID_LAYOUTS as readonly string[]).includes(v)
}

/** Space each tile takes besides its own box (comfortable density: 4 px
 * margin on both sides). Other densities pass their own (theme.ts). */
export const TILE_GAP = 8

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

export interface RowTile {
  index: number // position in the flat item list
  width: number // tile box width (px), margin excluded
  /** Columns layout only: the tile's own height (tile box) and where its
   * margin box sits, from the content's left edge and from its row's top.
   * Rows and square tiles flow left to right at the row's height instead. */
  height?: number
  x?: number
  y?: number
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

function squareRows(count: number, avail: number, size: number, gap: number): Row[] {
  // same maths as the original fixed grid: size 200 -> 210 px cells
  const cols = Math.max(2, Math.floor(avail / (size + gap + 2)))
  const tile = Math.floor(avail / cols) - gap
  const rows: Row[] = []
  for (let start = 0; start < count; start += cols) {
    const tiles: RowTile[] = []
    for (let i = start; i < Math.min(start + cols, count); i++) tiles.push({ index: i, width: tile })
    rows.push({ tiles, height: tile })
  }
  return rows
}

/** Columns (masonry) keep each image's own shape within these bounds;
 * only shapes beyond them crop (a 1:10 scroll would be a 2,000 px tile). */
const COL_MIN_ASPECT = 1 / 3
const COL_MAX_ASPECT = 5
const COL_MIN_HEIGHT = 24

/** A Columns tile's shape: the image's own ratio, loosely clamped. */
export function columnAspect(item: Sized): number {
  if (!item.has_thumb || !item.width || !item.height) return 1
  return Math.min(COL_MAX_ASPECT, Math.max(COL_MIN_ASPECT, item.width / item.height))
}

/**
 * Columns (masonry, Eagle's "Waterfall"): fixed-width columns, every image
 * uncropped at its own height, each item placed in the shortest column
 * (leftmost on a tie). Placing in list order keeps the DOM, Tab and
 * reading order left to right, row by row, and an appended page never
 * moves the tiles above it.
 *
 * The result is still a list of Rows so the virtualizer, the date scrubber
 * and the zoom anchor work unchanged: each Row is a horizontal band of
 * consecutive items. An item's top only ever grows with its index (it lands
 * on the lowest column, and columns only grow), so a band is a contiguous
 * index range, and a band starts at its first item's top. Band height is
 * the distance to the next band (minus the gap), the last band reaches the
 * longest column. Tiles overhang their band downwards; rowReach() tells the
 * virtualizer which earlier bands still reach into view.
 */
function columnRows(items: Sized[], avail: number, size: number, gap: number): Row[] {
  // same column count and width as square tiles of this size
  const cols = Math.max(2, Math.floor(avail / (size + gap + 2)))
  const w = Math.floor(avail / cols) - gap
  if (w <= 0) return []
  const bottoms = new Array<number>(cols).fill(0) // next free y per column (margin box)
  const tops: number[] = []
  const col: number[] = []
  const hs: number[] = []
  for (const it of items) {
    let c = 0
    for (let k = 1; k < cols; k++) if (bottoms[k] < bottoms[c]) c = k
    const h = Math.max(COL_MIN_HEIGHT, Math.round(w / columnAspect(it)))
    tops.push(bottoms[c])
    col.push(c)
    hs.push(h)
    bottoms[c] += h + gap
  }
  const end = Math.max(...bottoms)
  // bands at least half a column wide, and always taller than the gap
  const minBand = Math.max(gap + 1, Math.round(w / 2))
  const rows: Row[] = []
  let s = 0
  while (s < items.length) {
    let e = s + 1
    while (e < items.length && tops[e] < tops[s] + minBand) e++
    const next = e < items.length ? tops[e] : end
    const tiles: RowTile[] = []
    for (let k = s; k < e; k++) {
      tiles.push({ index: k, width: w, height: hs[k], x: col[k] * (w + gap), y: tops[k] - tops[s] })
    }
    rows.push({ tiles, height: next - tops[s] - gap })
    s = e
  }
  return rows
}

/**
 * For each row, the first row whose tiles still reach down into it (itself
 * when nothing above overhangs). Only Columns tiles overhang their row; the
 * virtualizer must render from there so a tall tile that starts above the
 * screen isn't dropped while its lower part is in view.
 */
export function rowReach(rows: Row[], gap: number = TILE_GAP): number[] {
  const starts = rowStarts(rows, gap)
  const bottom = rows.map((r, i) =>
    r.tiles.reduce((m, t) => Math.max(m, starts[i] + (t.y ?? 0) + (t.height ?? r.height) + gap), starts[i] + r.height + gap),
  )
  const tallest = rows.reduce((m, r) => r.tiles.reduce((mm, t) => Math.max(mm, (t.y ?? 0) + (t.height ?? r.height)), m), 0)
  return rows.map((_, i) => {
    let first = i
    for (let j = i - 1; j >= 0 && starts[i] - starts[j] <= tallest + gap; j--) {
      if (bottom[j] > starts[i]) first = j
    }
    return first
  })
}

/** Widths that fill `space` exactly, proportional to the aspects. */
function fillWidths(aspects: number[], space: number): number[] {
  const sum = aspects.reduce((a, b) => a + b, 0)
  const widths = aspects.map((r) => Math.max(1, Math.floor((r / sum) * space)))
  let rest = space - widths.reduce((a, b) => a + b, 0)
  for (let i = 0; rest > 0; i = (i + 1) % widths.length, rest--) widths[i]++
  return widths
}

function justifiedRows(items: Sized[], avail: number, target: number, gap: number): Row[] {
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
      const fitWith = (avail - gap * (aspects.length + 1)) / (sum + r)
      if (fitWith <= target) {
        // adding this tile fills the row; keep it if that lands closer to the
        // target height than ending the row without it
        const fitWithout =
          aspects.length > 0 ? (avail - gap * aspects.length) / sum : Number.POSITIVE_INFINITY
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
    const space = avail - gap * aspects.length
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

/** The phone's 1-column feed: every item on its own full-width row, in its
 * own (clamped) shape. */
export function feedRows(items: Sized[], avail: number, gap: number = TILE_GAP): Row[] {
  const w = avail - gap
  if (w <= 0) return []
  return items.map((it, index) => ({
    tiles: [{ index, width: w }],
    height: Math.max(40, Math.round(w / tileAspect(it))),
  }))
}

/** Each row's top in content px (rows are `height + gap` apart), after
 * `padStart` px of space above the first row. */
export function rowStarts(rows: Row[], gap: number = TILE_GAP, padStart = 0): number[] {
  const out: number[] = []
  let y = padStart
  for (const r of rows) {
    out.push(y)
    y += r.height + gap
  }
  return out
}

/**
 * Pack items into rows. `avail` is the usable content width; `size` is the
 * target tile height (justified), tile edge (square) or column width
 * (columns: the slider sets how wide a column is, as with square tiles).
 */
export function layoutRows(
  items: Sized[],
  avail: number,
  size: number,
  mode: GridLayout,
  gap: number = TILE_GAP,
): Row[] {
  if (items.length === 0 || avail <= 0) return []
  if (mode === 'square') return squareRows(items.length, avail, size, gap)
  if (mode === 'columns') return columnRows(items, avail, size, gap)
  return justifiedRows(items, avail, size, gap)
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
export function mobileTileSize(avail: number, cols: number, aspect = 1, gap: number = TILE_GAP): number {
  return Math.max(40, Math.floor((avail / cols - gap - 2) / aspect))
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
  /** Columns layout only: the tile's column, its margin box's top within
   * the row, and its own height */
  col?: number
  top?: number
  height?: number
}

/** Item index -> its row and horizontal centre, from layoutRows' output. */
export function tilePositions(rows: Row[], gap: number = TILE_GAP): TilePos[] {
  const pos: TilePos[] = []
  rows.forEach((row, r) => {
    let x = 0
    for (const t of row.tiles) {
      if (t.x !== undefined) {
        pos[t.index] = {
          row: r,
          center: t.x + gap / 2 + t.width / 2,
          col: Math.round(t.x / (t.width + gap)),
          top: t.y ?? 0,
          height: t.height ?? row.height,
        }
        continue
      }
      pos[t.index] = { row: r, center: x + gap / 2 + t.width / 2 }
      x += t.width + gap
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
 * In Columns, up/down move within the tile's own column (the tile directly
 * above or below it, which is also the nearest one on screen) and stay put
 * at the column's ends; left/right still follow reading order, so Shift+
 * arrows select the same runs as a Shift+click.
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
    if (here.col !== undefined) {
      const step = key === 'ArrowDown' ? 1 : -1
      for (let k = idx + step; k >= 0 && k < count; k += step) {
        if (positions[k]?.col === here.col) return k
      }
      return idx
    }
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

/**
 * The scroll offset that brings a tile spanning [top, bottom) (content px)
 * into view with as little movement as possible, keeping `padEnd` px clear
 * at the bottom (the bulk bar and toasts float there). Used for Columns
 * tiles, which can be taller than their virtualizer row.
 */
export function revealScrollTop(scrollTop: number, viewH: number, top: number, bottom: number, padEnd = 80): number {
  if (top < scrollTop) return Math.max(0, top)
  const room = viewH - padEnd
  if (bottom > scrollTop + room) return Math.max(0, Math.min(top, bottom - room))
  return scrollTop
}
