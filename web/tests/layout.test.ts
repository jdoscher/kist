// Grid layout tests. Run with `npm test` (node --test; Node strips the types).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  columnAspect,
  isGridLayout,
  layoutRows,
  mobileTileSize,
  nextIndex,
  pickThumbSize,
  revealScrollTop,
  rowReach,
  rowStarts,
  tileAspect,
  tilePositions,
  type Row,
} from '../src/layout.ts'
import { readPrefs, savePref } from '../src/settings.ts'
import { geometry, offsetOfPosition, perItemHeight, positionAtOffset, scrubModel } from '../src/scrub.ts'

const it = (w: number | null, h: number | null, hasThumb = true) => ({
  width: w,
  height: h,
  has_thumb: hasThumb,
})

const shapes = [
  it(1600, 1200),
  it(1206, 2622), // phone screenshot, clamps to 1:2
  it(4000, 1000), // panorama, clamps to 3:1
  it(1000, 1000),
  it(null, null, false), // no preview: square
  it(1920, 1080),
  it(800, 1200),
  it(3000, 500),
  it(1200, 1600),
  it(2048, 1366),
  it(500, 500),
]
const library = Array.from({ length: 157 }, (_, i) => shapes[(i * 7) % shapes.length])
const indexes = library.map((_, i) => i)

test('justified rows keep every item in order, fill the width, and flex at most 15%', () => {
  for (const avail of [600, 1000, 1500, 2400]) {
    for (const size of [120, 200, 400]) {
      const rows = layoutRows(library, avail, size, 'justified')
      assert.deepEqual(
        rows.flatMap((r) => r.tiles.map((t) => t.index)),
        indexes,
      )
      rows.forEach((r, k) => {
        const used = r.tiles.reduce((a, t) => a + t.width + 8, 0)
        const partialLast = k === rows.length - 1 && used !== avail
        if (partialLast) {
          assert.equal(r.height, size, 'last partial row keeps the normal height')
          assert.ok(used <= avail || r.tiles.length === 1, 'last row does not overflow')
        } else {
          assert.equal(used, avail, `row fills the width (avail ${avail}, size ${size})`)
          assert.ok(
            r.height >= Math.round(size * 0.85) && r.height <= Math.round(size * 1.15),
            `row height ${r.height} within 15% of ${size}`,
          )
        }
      })
    }
  }
})

test('square mode keeps square tiles and matches the original grid at 200 px', () => {
  const original = (w: number) => {
    const cols = Math.max(2, Math.floor((w - 16) / 210))
    return Math.floor((w - 16) / cols) - 8
  }
  for (const w of [700, 1100, 1600, 2560]) {
    assert.equal(layoutRows([it(1, 1)], w - 16, 200, 'square')[0].height, original(w))
  }
  const rows = layoutRows(library, 1500, 260, 'square')
  assert.deepEqual(
    rows.flatMap((r) => r.tiles.map((t) => t.index)),
    indexes,
  )
  rows.forEach((r) => r.tiles.forEach((t) => assert.equal(t.width, r.height)))
})

test('extreme shapes clamp to 1:2 .. 3:1; unknown shapes are square', () => {
  assert.equal(tileAspect(it(1206, 2622)), 0.5)
  assert.equal(tileAspect(it(9000, 1000)), 3)
  assert.equal(tileAspect(it(null, null, false)), 1)
  assert.equal(tileAspect(it(1600, 1200)), 4 / 3)
})

test('thumbnail choice covers the tile and prefers the smaller file', () => {
  // thumb_512 of a 2:1 image is 1024x512: covers a 400x200 tile at 2x
  assert.equal(pickThumbSize(it(2000, 1000), 400, 200, 2), 512)
  // thumb_1024 would be the same 1024x512, so no reason to switch
  assert.equal(pickThumbSize(it(2000, 1000), 920, 460, 2), 512)
  // square image at a big tile: 512 px is too small, 1024 covers better
  assert.equal(pickThumbSize(it(1000, 1000), 460, 460, 2), 1024)
})

test('tilePositions: row and centre of every tile', () => {
  const rows = [
    { tiles: [{ index: 0, width: 100 }, { index: 1, width: 200 }], height: 100 },
    { tiles: [{ index: 2, width: 300 }], height: 100 },
  ]
  assert.deepEqual(tilePositions(rows), [
    { row: 0, center: 54 },
    { row: 0, center: 212 },
    { row: 1, center: 154 },
  ])
})

test('nextIndex: arrows move through the grid like the eye expects', () => {
  // row 0: three 100 px tiles; row 1: one wide tile then a narrow one
  const rows = [
    { tiles: [0, 1, 2].map((index) => ({ index, width: 100 })), height: 100 },
    { tiles: [{ index: 3, width: 250 }, { index: 4, width: 50 }], height: 100 },
  ]
  const n = 5
  assert.equal(nextIndex('ArrowRight', 1, n, rows), 2)
  assert.equal(nextIndex('ArrowRight', 4, n, rows), 4) // stays on the last
  assert.equal(nextIndex('ArrowLeft', 0, n, rows), 0)
  assert.equal(nextIndex('ArrowLeft', -1, n, rows), 0) // nothing focused yet
  assert.equal(nextIndex('ArrowRight', -1, n, rows), 0)
  assert.equal(nextIndex('ArrowDown', -1, n, rows), 0)
  assert.equal(nextIndex('ArrowDown', 0, n, rows), 3) // nearest centre below
  assert.equal(nextIndex('ArrowDown', 2, n, rows), 4)
  assert.equal(nextIndex('ArrowUp', 4, n, rows), 2)
  assert.equal(nextIndex('ArrowUp', 3, n, rows), 1)
  assert.equal(nextIndex('ArrowUp', 1, n, rows), 1) // top row: stay
  assert.equal(nextIndex('ArrowDown', 3, n, rows), 3) // bottom row: stay
  assert.equal(nextIndex('ArrowDown', 0, 0, []), -1) // empty grid
})

test('nextIndex agrees with a real justified layout', () => {
  const rows = layoutRows(library, 1000, 200, 'justified')
  const pos = tilePositions(rows)
  for (let i = 0; i < library.length; i++) {
    const down = nextIndex('ArrowDown', i, library.length, rows, pos)
    if (pos[i].row < rows.length - 1) assert.equal(pos[down].row, pos[i].row + 1)
    else assert.equal(down, i)
  }
})

// ---- Columns (masonry, B22)

/** Every Columns tile in absolute content px (margin box). */
function placed(rows: Row[], gap = 8) {
  const starts = rowStarts(rows, gap)
  return rows.flatMap((r, k) =>
    r.tiles.map((t) => ({ index: t.index, row: k, x: t.x!, top: starts[k] + t.y!, h: t.height!, w: t.width })),
  )
}

test('columns: fixed-width columns, items in order, uncropped, shortest column next', () => {
  for (const avail of [360, 600, 1000, 1500, 2400]) {
    for (const size of [120, 200, 400]) {
      for (const gap of [2, 8, 12]) {
        const rows = layoutRows(library, avail, size, 'columns', gap)
        const tiles = placed(rows, gap)
        // DOM / reading order is the list order
        assert.deepEqual(tiles.map((t) => t.index), indexes)
        // column count and width as square tiles of the same size (2 at least)
        const sq = layoutRows(library, avail, size, 'square', gap)[0]
        const w = sq.height
        assert.ok(tiles.every((t) => t.w === w), 'one column width')
        const cols = Math.max(2, Math.floor(avail / (size + gap + 2)))
        assert.equal(new Set(tiles.map((t) => t.x)).size, Math.min(cols, library.length))
        assert.ok(tiles.every((t) => t.x + t.w + gap <= avail), 'columns fit the width')
        // replay the placement: each item goes on the shortest column (leftmost on a tie)
        const bottoms = new Array(cols).fill(0)
        for (const t of tiles) {
          const c = bottoms.indexOf(Math.min(...bottoms))
          assert.equal(t.x, c * (w + gap), `item ${t.index} in the shortest column`)
          assert.equal(t.top, bottoms[c], `item ${t.index} sits right under the one above`)
          bottoms[c] += t.h + gap
          // uncropped: the tile has the image's own shape (within 1 px rounding)
          assert.ok(Math.abs(t.h - Math.max(24, Math.round(w / columnAspect(library[t.index])))) === 0)
        }
        // the content is exactly as tall as the longest column
        const last = rows.length - 1
        assert.equal(rowStarts(rows, gap)[last] + rows[last].height + gap, Math.max(...bottoms))
        // bands: contiguous index runs with positive height, tops never go back up
        rows.forEach((r) => assert.ok(r.height > 0, 'band height > 0'))
        for (let i = 1; i < tiles.length; i++) assert.ok(tiles[i].top >= tiles[i - 1].top)
      }
    }
  }
})

test('columns: only very extreme shapes crop; unknown shapes are square', () => {
  assert.equal(columnAspect(it(1206, 2622)), 1206 / 2622) // phone screenshot keeps its shape
  assert.equal(columnAspect(it(4000, 1000)), 4) // panorama keeps its shape
  assert.equal(columnAspect(it(1000, 10000)), 1 / 3)
  assert.equal(columnAspect(it(20000, 1000)), 5)
  assert.equal(columnAspect(it(null, null, false)), 1)
})

test('columns: a loaded page appended below never moves the tiles above', () => {
  const first = placed(layoutRows(library.slice(0, 100), 1200, 200, 'columns'))
  const all = placed(layoutRows(library, 1200, 200, 'columns'))
  for (const t of first) {
    const u = all[t.index]
    assert.deepEqual([u.x, u.top, u.h], [t.x, t.top, t.h])
  }
})

test('rowReach: every row a tile overlaps is rendered from the tile\'s own row', () => {
  for (const mode of ['columns', 'justified', 'square'] as const) {
    const rows = layoutRows(library, 1000, 160, mode)
    const reach = rowReach(rows)
    const starts = rowStarts(rows)
    if (mode !== 'columns') {
      assert.deepEqual(reach, rows.map((_, i) => i), `${mode} rows never overhang`)
      continue
    }
    for (const t of placed(rows)) {
      rows.forEach((_, i) => {
        const bandTop = starts[i]
        const bandEnd = i + 1 < rows.length ? starts[i + 1] : Infinity
        const overlaps = t.top < bandEnd && t.top + t.h + 8 > bandTop
        if (overlaps && i >= t.row) assert.ok(reach[i] <= t.row, `row ${i} renders tile ${t.index} (row ${t.row})`)
      })
    }
    assert.ok(reach.some((r, i) => r < i), 'tall tiles do overhang')
  }
})

test('columns: up/down stay in the column, left/right follow reading order', () => {
  const rows = layoutRows(library, 1000, 200, 'columns')
  const pos = tilePositions(rows)
  const n = library.length
  const byCol = new Map<number, number[]>()
  pos.forEach((p, i) => byCol.set(p.col!, [...(byCol.get(p.col!) ?? []), i]))
  assert.ok(byCol.size >= 2)
  for (const list of byCol.values()) {
    list.forEach((i, k) => {
      assert.equal(nextIndex('ArrowDown', i, n, rows, pos), list[k + 1] ?? i, 'down: the tile under it')
      assert.equal(nextIndex('ArrowUp', i, n, rows, pos), list[k - 1] ?? i, 'up: the tile over it')
    })
  }
  assert.equal(nextIndex('ArrowRight', 5, n, rows, pos), 6)
  assert.equal(nextIndex('ArrowLeft', 5, n, rows, pos), 4)
  assert.equal(nextIndex('ArrowDown', -1, n, rows, pos), 0)
  // the tile directly below in its column is also the nearest one below on screen
  const starts = rowStarts(rows)
  const top = (i: number) => starts[pos[i].row] + pos[i].top!
  for (const list of byCol.values()) for (let k = 1; k < list.length; k++) assert.ok(top(list[k]) > top(list[k - 1]))
})

test('revealScrollTop scrolls a tall tile into view by the least amount', () => {
  assert.equal(revealScrollTop(500, 800, 600, 900), 500) // already in view
  assert.equal(revealScrollTop(500, 800, 300, 500), 300) // above: its top
  assert.equal(revealScrollTop(500, 800, 1000, 1300), 580) // below: bottom clear of the bulk bar
  assert.equal(revealScrollTop(0, 800, 1000, 3000), 1000) // taller than the screen: its top
})

test('Columns is a stored tile shape; Rows stays the default', () => {
  const mem = new Map<string, string>()
  const s = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) }
  assert.equal(readPrefs(s as never).gridLayout, 'justified')
  savePref('gridLayout', 'columns', s as never)
  assert.equal(readPrefs(s as never).gridLayout, 'columns')
  mem.set('kist.gridLayout', 'waterfall')
  assert.equal(readPrefs(s as never).gridLayout, 'justified')
  assert.ok(isGridLayout('columns') && isGridLayout('square') && !isGridLayout('rows'))
})

test('columns on a phone: the chosen count, and never fewer than 2', () => {
  for (const cols of [2, 3, 4, 6]) {
    const rows = layoutRows(library, 374, mobileTileSize(374, cols, 1, 8), 'columns', 8)
    assert.equal(new Set(placed(rows).map((t) => t.x)).size, cols)
  }
  assert.equal(new Set(placed(layoutRows(library, 200, 400, 'columns', 8)).map((t) => t.x)).size, 2)
})

test('the date scrubber maths works on Columns bands', () => {
  const rows = layoutRows(library, 1000, 200, 'columns')
  const starts = rowStarts(rows)
  const first = rows.map((r) => r.tiles[0].index)
  const heights = rows.map((r) => r.height + 8)
  const per = perItemHeight(rows, 8, { size: 200, avail: 1000 })
  assert.ok(per > 0 && per < 200, `per-item height ${per}`)
  const m = scrubModel({ buckets: [{ key: '2024-01', count: 100, cursor: '' }, { key: '2023-12', count: 157, cursor: 'c' }] })
  const g = geometry(m, 0, library.length, starts.at(-1)! + rows.at(-1)!.height + 8, per, false)
  let prev = -1
  for (let y = 0; y < g.loadedHeight; y += 37) {
    const p = positionAtOffset(g, y, starts, first, heights)
    assert.ok(p >= prev, 'positions grow down the page')
    prev = p
  }
  rows.forEach((_, k) => assert.equal(offsetOfPosition(g, first[k], starts, first), starts[k]))
})
