// F4: credits and rights, the AI-made flag and the print check (no React).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { emptyQuery, itemsParams } from '../src/api.ts'
import {
  aiReason,
  bulkCreditsPatch,
  cleanCreditFilter,
  cleanPrintSize,
  clearanceText,
  creditChipLabel,
  creditLine,
  creditsTileLabel,
  emptyBulkCredits,
  fitsText,
  isAiMade,
  isNeedsCreditView,
  needsCredit,
  profileText,
  rightsText,
  sizeText,
  statusBreakdown,
  type Clearance,
  type PrintInfo,
} from '../src/credits.ts'
import { cleanFilter, filterChipLabel, hasM45Filters, m45Filters } from '../src/m45/filters.ts'
import { smartFolderQuery } from '../src/query.ts'
import { fromUrl, toUrl } from '../src/url.ts'

test('credit line matches the server rule', () => {
  assert.equal(creditLine({}), null)
  assert.equal(creditLine({ creator: 'Ann' }), 'Ann')
  assert.equal(creditLine({ creator: 'Ann', credit: 'AP' }), 'Ann / AP')
  assert.equal(creditLine({ creator: 'Ann', credit: 'Ann for AP' }), 'Ann for AP')
  assert.equal(needsCredit({}), true)
  assert.equal(needsCredit({ credit_exempt: true }), false)
  assert.equal(needsCredit({ credit: 'x' }), false)
  assert.equal(rightsText('ref_only'), 'Ref only')
  assert.equal(rightsText('bogus'), 'Not set')
})

test('AI-made is a flag with a reason, never a filter-out', () => {
  assert.equal(isAiMade({ ai_generated: false }), false)
  assert.equal(aiReason({ ai_generated: true, c2pa: true }), 'Content Credentials (C2PA)')
  assert.equal(aiReason({ ai_generated: true, ai_generator: 'Midjourney' }), 'Midjourney (named in the file’s metadata)')
  assert.equal(aiReason({ ai_generated: true }), 'Marked by hand')
  assert.equal(creditsTileLabel({ ai_generated: true }), ', AI-made')
  assert.equal(creditsTileLabel(null), '')
})

test('credit filters are cleaned and ride with the M4/M5 filters', () => {
  assert.equal(cleanCreditFilter('rights_status', 'Comp, cleared'), 'comp,cleared')
  assert.equal(cleanCreditFilter('rights_status', 'maybe'), null)
  assert.equal(cleanCreditFilter('needs_credit', '1'), 'true')
  assert.equal(cleanCreditFilter('needs_credit', 'x'), null)
  assert.equal(cleanPrintSize('a4'), 'A4')
  assert.equal(cleanPrintSize('20 cm'), '20cm')
  assert.equal(cleanPrintSize('huge'), null)
  assert.equal(cleanFilter('print_min', '8IN'), '8in')
  const q = { ...emptyQuery, needs_credit: 'true', rights_status: 'uncleared', print_max: 'A4' }
  assert.deepEqual(m45Filters(q), { rights_status: 'uncleared', needs_credit: 'true', print_max: 'A4' })
  assert.equal(hasM45Filters(q), true)
  const p = itemsParams(q)
  assert.equal(p.get('needs_credit'), 'true')
  assert.equal(p.get('rights_status'), 'uncleared')
  assert.equal(p.get('print_max'), 'A4')
  // smart folders save them under the server's names
  const saved = smartFolderQuery(q) as Record<string, unknown>
  assert.equal(saved.needs_credit, 'true')
  assert.equal(saved.print_max, 'A4')
  // chips
  assert.equal(filterChipLabel('needs_credit', 'true'), 'Needs a credit')
  assert.equal(creditChipLabel('rights_status', 'comp,none'), 'Comp or No rights status')
  assert.equal(filterChipLabel('print_min', 'A4'), 'Prints A4 at 300 dpi')
  assert.equal(isNeedsCreditView(q), true)
})

test('the URL keeps the credit filters', () => {
  const place = fromUrl('/all', '?needs_credit=true&print_min=A3')
  assert.equal(place.query.needs_credit, 'true')
  assert.equal(place.query.print_min, 'A3')
  const url = toUrl(place)
  assert.match(url, /needs_credit=true/)
  assert.match(url, /print_min=A3/)
})

const info: PrintInfo = {
  width: 3000,
  height: 2000,
  sizes: { '300': { width: { in: 10, cm: 25.4 }, height: { in: 6.67, cm: 16.9 } }, '150': { width: { in: 20, cm: 50.8 }, height: { in: 13.33, cm: 33.9 } } },
  fits: { '300': ['A5'], '150': ['A5', 'A4', 'A3', 'Letter', 'Tabloid'] },
  low_res: false,
  icc: 'sRGB IEC61966-2.1',
  colour_space: 'RGB',
  file_dpi: 72,
  untagged: false,
}

test('print check text', () => {
  assert.equal(sizeText(info, 300, 'in'), '10.0 × 6.7 in')
  assert.equal(sizeText(info, 300, 'cm'), '25.4 × 16.9 cm')
  assert.equal(fitsText(info, 300), 'fills A5')
  assert.equal(fitsText(info, 150), 'fills A3')
  assert.equal(fitsText({ ...info, fits: { '300': [] } }, 300), 'smaller than A5')
  assert.equal(profileText(info), 'sRGB IEC61966-2.1 (RGB)')
  assert.equal(profileText({ ...info, icc: null, untagged: true }), 'None (untagged; shown as sRGB)')
  assert.equal(profileText({ ...info, icc: null, colour_space: 'CMYK', untagged: false }), 'CMYK, no profile')
})

test('clearance warning wording', () => {
  const c: Clearance = { enabled: true, total: 3, uncleared: 2, by_status: { comp: 1, none: 1 }, items: [] }
  assert.equal(clearanceText(c), '2 of 3 items aren’t marked licensed or cleared.')
  assert.equal(clearanceText({ ...c, uncleared: 3 }), 'None of these 3 items are marked licensed or cleared.')
  assert.equal(clearanceText({ ...c, total: 1, uncleared: 1 }), 'This item isn’t marked licensed or cleared.')
  assert.equal(clearanceText({ ...c, uncleared: 0 }), null)
  assert.equal(clearanceText({ ...c, enabled: false }), null)
  assert.equal(statusBreakdown(c), '1 comp, 1 not set')
})

test('bulk credits send only what was filled in', () => {
  assert.deepEqual(bulkCreditsPatch(emptyBulkCredits), {})
  assert.deepEqual(bulkCreditsPatch({ ...emptyBulkCredits, credit: ' AP ', rights_status: 'cleared' }), { credit: 'AP', rights_status: 'cleared' })
  assert.deepEqual(bulkCreditsPatch({ ...emptyBulkCredits, rights_status: 'clear', credit_exempt: 'no' }), {
    rights_status: null,
    credit_exempt: null,
  })
  assert.deepEqual(bulkCreditsPatch({ ...emptyBulkCredits, credit_exempt: 'yes' }), { credit_exempt: true })
})

test('credits.css keeps to its prefix and existing tokens', () => {
  const css = readFileSync(new URL('../src/credits.css', import.meta.url), 'utf8')
  for (const m of css.matchAll(/^\.([a-zA-Z][\w-]*)/gm)) assert.ok(m[1].startsWith('cr-') || m[1] === 'tile' || m[1] === 'mobile', m[1])
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, 'colours come from tokens only')
  assert.doesNotMatch(css, /--accent\)|--warn\)|--danger/, 'no brass or danger styling for information')
})
