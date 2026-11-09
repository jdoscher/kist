// Tile placeholders: ThumbHash, then dominant colour, then nothing. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { rgbaToThumbHash } from 'thumbhash'
import { normalizeHex, placeholderFor, placeholderStyle, thumbhashUrl } from '../src/placeholder.ts'

function sampleHash(): string {
  // an 8x8 orange-to-blue gradient
  const w = 8
  const h = 8
  const rgba = new Uint8Array(w * h * 4)
  for (let i = 0; i < w * h; i++) rgba.set([240 - i * 3, 120, i * 3, 255], i * 4)
  return btoa(String.fromCharCode(...rgbaToThumbHash(w, h, rgba)))
}

test('a ThumbHash wins and decodes to a PNG data URL', () => {
  const p = placeholderFor({ thumbhash: sampleHash(), dominant_colors: [{ hex: '#123456', pct: 50 }] })
  assert.equal(p?.kind, 'thumbhash')
  assert.match((p as { url: string }).url, /^data:image\/png;base64,/)
  assert.match(placeholderStyle(p).backgroundImage ?? '', /^url\("data:image\/png/)
})

test('no or broken ThumbHash falls back to the first dominant colour', () => {
  const colours = [{ hex: '#ABC', pct: 60 }, { hex: '#000000', pct: 40 }]
  assert.deepEqual(placeholderFor({ dominant_colors: colours }), { kind: 'colour', hex: '#aabbcc' })
  assert.deepEqual(placeholderFor({ thumbhash: null, dominant_colors: colours }), { kind: 'colour', hex: '#aabbcc' })
  assert.deepEqual(placeholderFor({ thumbhash: '%%%not base64', dominant_colors: colours }), { kind: 'colour', hex: '#aabbcc' })
  assert.deepEqual(placeholderFor({ thumbhash: 'AA==', dominant_colors: colours }), { kind: 'colour', hex: '#aabbcc' })
  assert.equal(thumbhashUrl('%%%not base64'), null)
  assert.deepEqual(placeholderStyle({ kind: 'colour', hex: '#aabbcc' }), { backgroundColor: '#aabbcc' })
})

test('nothing usable: no placeholder (the theme colour shows)', () => {
  assert.equal(placeholderFor({}), null)
  assert.equal(placeholderFor({ dominant_colors: null }), null)
  assert.equal(placeholderFor({ dominant_colors: [] }), null)
  assert.equal(placeholderFor({ dominant_colors: [{ hex: 'red', pct: 1 }] }), null)
  assert.deepEqual(placeholderStyle(null), {})
})

test('normalizeHex accepts #rgb / rrggbb only', () => {
  assert.equal(normalizeHex('#FfF'), '#ffffff')
  assert.equal(normalizeHex('a1b2c3'), '#a1b2c3')
  assert.equal(normalizeHex(' #a1b2c3 '), '#a1b2c3')
  assert.equal(normalizeHex('#a1b2c3d4'), null)
  assert.equal(normalizeHex(42), null)
})
