// Every theme's colour pairs meet WCAG 2.2 AA, read straight from themes.css.
// Run with `npm test`; `node --experimental-strip-types scripts/contrast.ts` prints the table.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { checkThemes, contrast, parseThemes } from '../src/contrast.ts'

const css = readFileSync(new URL('../src/themes.css', import.meta.url), 'utf8')

test('contrast maths matches known values', () => {
  assert.equal(contrast('#000000', '#ffffff').toFixed(2), '21.00')
  assert.equal(contrast('#767676', '#ffffff').toFixed(2), '4.54')
  assert.equal(contrast('#fff', '#fff'), 1)
})

test('themes.css defines dark, light and gallery with the same tokens', () => {
  const t = parseThemes(css)
  assert.deepEqual(Object.keys(t).sort(), ['dark', 'gallery', 'light'])
  const keys = Object.keys(t.dark).sort()
  assert.deepEqual(Object.keys(t.light).sort(), keys)
  assert.deepEqual(Object.keys(t.gallery).sort(), keys)
})

test('every text and UI colour pair passes in every theme', () => {
  const failing = checkThemes(parseThemes(css)).filter((r) => !r.ok)
  assert.deepEqual(
    failing.map((r) => `${r.theme}: --${r.fg} on --${r.bg} ${r.ratio.toFixed(2)} < ${r.min}`),
    [],
  )
})

test('gallery canvas is the 18% grey', () => {
  assert.equal(parseThemes(css).gallery.canvas, '#767676')
})
