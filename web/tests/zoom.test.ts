// Grid zoom (M6 W1.2/W1.3): phone pinch steps, ⌘/Ctrl+wheel size, anchoring.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { feedRows, layoutRows, mobileTileSize, rowStarts } from '../src/layout.ts'
import { readPrefs, savePref } from '../src/settings.ts'
import {
  anchoredScrollTop,
  parsePhoneCols,
  pinchCols,
  PHONE_COLS,
  sliderStep,
  stepCols,
  touchDistance,
  wheelSize,
} from '../src/zoom.ts'

test('pinch steps through 1, 2, 3, 4, 6 columns', () => {
  assert.deepEqual([...PHONE_COLS], [1, 2, 3, 4, 6])
  // fingers apart: fewer, bigger columns
  assert.equal(pinchCols(3, 1.6), 2)
  assert.equal(pinchCols(3, 3.5), 1)
  assert.equal(pinchCols(2, 2.2), 1)
  // fingers together: more columns
  assert.equal(pinchCols(3, 0.7), 4)
  assert.equal(pinchCols(4, 0.6), 6)
  assert.equal(pinchCols(3, 0.4), 6)
  // the ends hold
  assert.equal(pinchCols(1, 5), 1)
  assert.equal(pinchCols(6, 0.1), 6)
})

test('a small wobble keeps the current step', () => {
  for (const c of PHONE_COLS) {
    assert.equal(pinchCols(c, 1.08), c)
    assert.equal(pinchCols(c, 0.93), c)
    assert.equal(pinchCols(c, 1), c)
  }
  assert.equal(pinchCols(3, Number.NaN), 3)
  assert.equal(pinchCols(3, 0), 3)
})

test('the column buttons step one at a time and stop at the ends', () => {
  assert.equal(stepCols(1, -1), 1)
  assert.equal(stepCols(1, 1), 2)
  assert.equal(stepCols(4, 1), 6)
  assert.equal(stepCols(6, 1), 6)
})

test('stored columns accept every step and fall back to 3', () => {
  for (const c of PHONE_COLS) assert.equal(parsePhoneCols(String(c)), c)
  for (const bad of [null, undefined, '', '5', '0', 'x', '3.5']) assert.equal(parsePhoneCols(bad), 3)
  const m = new Map<string, string>()
  const s = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
  savePref('mobileCols', 6, s)
  assert.equal(readPrefs(s).mobileCols, 6)
  savePref('mobileCols', 1, s)
  assert.equal(readPrefs(s).mobileCols, 1)
})

test('mobile tile sizes give the chosen column count for 4 and 6 columns too', () => {
  const items = Array.from({ length: 30 }, () => ({ width: 800, height: 800, has_thumb: true }))
  const avail = 390 - 16
  for (const cols of [2, 3, 4, 6]) {
    const sq = layoutRows(items, avail, mobileTileSize(avail, cols, 1, 8), 'square', 8)
    assert.equal(sq[0].tiles.length, cols, `square ${cols}`)
    const ju = layoutRows(items, avail, mobileTileSize(avail, cols, 1, 8), 'justified', 8)
    assert.equal(ju[0].tiles.length, cols, `justified ${cols}`)
  }
})

test('1 column is a feed: one item a row, full width, its own shape', () => {
  const items = [
    { width: 1600, height: 900, has_thumb: true },
    { width: 900, height: 1600, has_thumb: true },
    { width: null, height: null, has_thumb: false },
    { width: 1000, height: 5000, has_thumb: true }, // clamps to 1:2
  ]
  const rows = feedRows(items, 374, 8)
  assert.equal(rows.length, 4)
  for (const [k, r] of rows.entries()) {
    assert.deepEqual(r.tiles, [{ index: k, width: 366 }])
  }
  assert.equal(rows[0].height, Math.round(366 / (16 / 9)))
  assert.equal(rows[1].height, Math.round(366 / (9 / 16)))
  assert.equal(rows[2].height, 366)
  assert.equal(rows[3].height, 732)
  assert.deepEqual(rowStarts(rows, 8, 100), [100, 100 + rows[0].height + 8, 100 + rows[0].height + rows[1].height + 16, 100 + rows[0].height + rows[1].height + rows[2].height + 24])
  assert.deepEqual(feedRows(items, 0), [])
})

test('⌘/Ctrl+wheel: up grows, down shrinks, pinch follows the fingers', () => {
  assert.ok(wheelSize(200, -100) > 200)
  assert.ok(wheelSize(200, 100) < 200)
  // one mouse notch is one moderate step, not the whole range
  assert.ok(wheelSize(200, -100) < 260)
  // a trackpad pinch: Chrome reports deltaY = -100·ln(scale)
  assert.ok(Math.abs(wheelSize(200, -100 * Math.log(1.05)) - 210) < 0.01)
  // many tiny pinch events add up
  let s = 200
  for (let i = 0; i < 20; i++) s = wheelSize(s, -100 * Math.log(1.02))
  assert.ok(Math.abs(s - 200 * 1.02 ** 20) < 0.5)
  // line-mode wheels (Firefox) and clamping to the slider's range
  assert.ok(wheelSize(200, -3, 1) > 200)
  assert.equal(wheelSize(390, -1000), 400)
  assert.equal(wheelSize(125, 1000), 120)
  assert.equal(sliderStep(204.9), 200)
  assert.equal(sliderStep(205), 210)
  assert.equal(sliderStep(999), 400)
})

test('the item under the fingers stays put after a relayout', () => {
  // pointer 300 px below the box top, 25% down a tile whose row now starts
  // at 2000 and is 400 tall: 2000 + 100 - 300
  assert.equal(anchoredScrollTop(2000, 400, 0.25, 300), 1800)
  assert.equal(anchoredScrollTop(100, 400, 0.5, 600), 0)
  assert.equal(anchoredScrollTop(1000, 200, 7, 0), 1200)
  assert.equal(touchDistance({ clientX: 0, clientY: 0 }, { clientX: 3, clientY: 4 }), 5)
})
