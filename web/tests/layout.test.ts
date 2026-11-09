// Grid layout tests. Run with `npm test` (node --test; Node strips the types).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { layoutRows, nextIndex, pickThumbSize, tileAspect, tilePositions } from '../src/layout.ts'

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
