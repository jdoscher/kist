// Full-screen preview source choice. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fullPreviewUrl, shownLongSide, type PreviewItem } from '../src/preview.ts'

const item = (w: number, h: number, ext = 'jpg', size = 20e6): PreviewItem => ({
  id: 'I', ext, width: w, height: h, size_bytes: size, preview_rev: 3,
})
const phone: [number, number] = [390 * 3, (844 - 120) * 3] // iPhone, 3x
const laptop: [number, number] = [(1440 - 140) * 2, (900 - 90) * 2] // 1440x900, 2x
const big: [number, number] = [(2560 - 140) * 2, (1440 - 90) * 2] // 27-inch, 2x

test('a phone never downloads the original', () => {
  assert.equal(fullPreviewUrl(item(8000, 8000), ...phone), '/api/items/I/thumb?size=2048&v=3')
  assert.equal(fullPreviewUrl(item(8000, 4030), ...phone), '/api/items/I/thumb?size=2048&v=3')
})

test('small images need nothing past the 1024 thumbnail', () => {
  assert.equal(fullPreviewUrl(item(1000, 800), ...big), null)
  // shown no bigger than 1024 px anyway
  assert.equal(fullPreviewUrl(item(4000, 3000), 900, 700), null)
})

test('laptops get the 2048 copy unless the image is shown wider than that', () => {
  assert.equal(shownLongSide(item(8000, 8000), ...laptop), 1620)
  assert.equal(fullPreviewUrl(item(8000, 8000), ...laptop), '/api/items/I/thumb?size=2048&v=3')
  assert.equal(fullPreviewUrl(item(8000, 4030), ...laptop), '/api/items/I/file')
})

test('big screens: original when it is reasonable, display copy for HEIC', () => {
  assert.equal(fullPreviewUrl(item(6000, 4000), ...big), '/api/items/I/file')
  assert.equal(fullPreviewUrl(item(6000, 4000, 'heic'), ...big), '/api/items/I/thumb?size=4096&v=3')
  assert.equal(fullPreviewUrl(item(9000, 6000, 'png', 90e6), ...big), '/api/items/I/thumb?size=2048&v=3')
  assert.equal(fullPreviewUrl(item(6000, 4000, 'tiff'), ...big), '/api/items/I/thumb?size=2048&v=3')
})
