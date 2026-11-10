// The phone toolbar's selects fit their labels (Kist 2.0 M6c). Run with
// `npm test`.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { chromeOf, fitSelectWidth, PHONE_ROW, phoneRowWidth } from '../src/fitSelect.ts'

const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8')
const grid = readFileSync(new URL('../src/m6grid.css', import.meta.url), 'utf8')
const block = (src: string, selector: string) => {
  const at = src.indexOf(`${selector} {`)
  assert.ok(at >= 0, `no ${selector} rule`)
  return src.slice(at, src.indexOf('}', at))
}

test('a select is as wide as its label plus padding and borders', () => {
  const c = chromeOf({ paddingLeft: '6px', paddingRight: '15px', borderLeftWidth: '1px', borderRightWidth: '1px' })
  assert.deepEqual(c, { paddingLeft: 6, paddingRight: 15, borderLeft: 1, borderRight: 1 })
  assert.equal(fitSelectWidth(37.4, c), 61)
  assert.equal(fitSelectWidth(NaN, c), 23)
})

test('the phone row fits Type, Rating and Newest in full on a 390 px phone', () => {
  // Inter Variable 16 px, measured on dev0's served build (canvas measureText)
  const defaults = [38, 48, 57] // Type, Rating, Newest
  assert.ok(phoneRowWidth(defaults) <= PHONE_ROW.width, `row ${phoneRowWidth(defaults)} > ${PHONE_ROW.width}`)
  // before: three equal flex boxes of 61 px left 31 px for each label
  assert.ok(61 - 30 < Math.min(...defaults))
})

test('the CSS uses the numbers fitSelect.ts budgets with', () => {
  const sel = block(css, '.mobile .topbar select')
  assert.match(sel, new RegExp(`padding: 0 ${PHONE_ROW.select.paddingRight}px 0 ${PHONE_ROW.select.paddingLeft}px`))
  assert.match(sel, /flex: 0 1 auto/) // shrinks when a long pick needs it, never grows
  assert.match(block(css, '.mobile .view-row'), new RegExp(`gap: ${PHONE_ROW.gap}px`))
  assert.match(block(css, '.mobile .view-row'), /display: flex/) // one line: no wrap onto a third row
  assert.match(block(css, '.view-row'), /display: contents/) // desktop unchanged
  assert.match(block(css, '.mobile .layout-toggle'), new RegExp(`width: ${PHONE_ROW.layoutToggle}px`))
  assert.match(block(css, '.mobile .view-row .filters-btn'), new RegExp(`min-width: ${PHONE_ROW.filters}px`))
  // the stepper's 26 px buttons must out-rank styles.css's 34 px
  // .mobile .cols-toggle button (same specificity, and styles.css loads later)
  assert.match(grid, /\.mobile \.cols-toggle\.cols-stepper button \{\s*min-width: 26px/)
  assert.equal(PHONE_ROW.stepper, 26 + 18 + 26)
})
