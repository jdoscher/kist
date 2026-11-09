// Phone layout helpers. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { layoutRows, mobileTileSize, typicalAspect } from '../src/layout.ts'
import { isPhoneLayout, MOBILE_QUERY, swipeDirection } from '../src/mobile.ts'

test('square tiles give exactly the chosen column count on phone widths', () => {
  const items = Array.from({ length: 12 }, () => ({ width: 800, height: 600, has_thumb: true }))
  for (const width of [320, 375, 390, 393, 430, 760]) {
    const avail = width - 16
    for (const cols of [2, 3]) {
      const rows = layoutRows(items, avail, mobileTileSize(avail, cols), 'square')
      assert.equal(rows[0].tiles.length, cols, `width ${width}, ${cols} cols`)
    }
  }
})

test('justified rows hold about the chosen number of square-ish images', () => {
  const items = Array.from({ length: 30 }, () => ({ width: 1000, height: 1000, has_thumb: true }))
  const avail = 390 - 16
  for (const cols of [2, 3]) {
    const rows = layoutRows(items, avail, mobileTileSize(avail, cols), 'justified')
    assert.equal(rows[0].tiles.length, cols)
  }
})

test('justified rows follow the folder\'s typical shape', () => {
  const avail = 390 - 16
  for (const [w, h] of [[1200, 1550], [1600, 900], [1000, 1000]]) {
    const items = Array.from({ length: 30 }, () => ({ width: w, height: h, has_thumb: true }))
    for (const cols of [2, 3]) {
      const size = mobileTileSize(avail, cols, typicalAspect(items))
      const rows = layoutRows(items, avail, size, 'justified')
      assert.equal(rows[0].tiles.length, cols, `${w}x${h}, ${cols} cols`)
    }
  }
  assert.equal(typicalAspect([]), 1)
})

test('swipes need distance and a clear direction', () => {
  assert.equal(swipeDirection(-80, 5), 'left')
  assert.equal(swipeDirection(90, -10), 'right')
  assert.equal(swipeDirection(4, 120), 'down')
  assert.equal(swipeDirection(0, -70), 'up')
  assert.equal(swipeDirection(30, 10), null) // too short: a tap
  assert.equal(swipeDirection(70, 60), null) // diagonal
})

test('the phone layout needs a touch screen: desktop zoom keeps the editing layout', () => {
  assert.equal(isPhoneLayout(390, 844, true), true) // iPhone upright
  assert.equal(isPhoneLayout(844, 390, true), true) // iPhone sideways
  assert.equal(isPhoneLayout(720, 450, false), false) // 1440x900 at 200% zoom
  assert.equal(isPhoneLayout(320, 225, false), false) // 1280x900 at 400% zoom
  assert.equal(isPhoneLayout(1440, 900, false), false)
  assert.equal(isPhoneLayout(1024, 1366, true), false) // a big tablet keeps the desktop layout
  // every clause of the media query asks for a coarse pointer
  for (const clause of MOBILE_QUERY.split(',')) assert.match(clause, /\(pointer: coarse\)/)
})
