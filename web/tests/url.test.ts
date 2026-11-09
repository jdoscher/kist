// URL <-> place mapping and Back behaviour. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, type Query } from '../src/api.ts'
import { fromUrl, historyAction, layerOf, toUrl, type Place } from '../src/url.ts'

const place = (query: Partial<Query>, extra: Partial<Place> = {}): Place => ({
  query: { ...emptyQuery, ...query },
  smartId: null,
  item: null,
  full: false,
  ...extra,
})

const roundTrip = (p: Place) => {
  const url = toUrl(p)
  const [path, search = ''] = url.split('?')
  return fromUrl(path, search ? `?${search}` : '')
}

test('every view has a readable URL', () => {
  assert.equal(toUrl(place({ view: 'unfiled' })), '/')
  assert.equal(toUrl(place({})), '/all')
  assert.equal(toUrl(place({ deleted: true })), '/trash')
  assert.equal(toUrl(place({ folder: 'F1' })), '/folder/F1')
  assert.equal(toUrl(place({ tags: ['old cars'] })), '/tag/old%20cars')
  assert.equal(toUrl(place({ tags: ['a', 'b'] })), '/all?tags=a%2Cb')
  assert.equal(toUrl(place({}, { smartId: 'S1' })), '/smart/S1')
  assert.equal(toUrl(place({ folder: 'F1' }, { item: 'I1', full: true })), '/folder/F1?item=I1&full=1')
})

test('URLs round-trip', () => {
  const cases: Place[] = [
    place({ view: 'unfiled', q: 'watch', type: 'image' }),
    place({}),
    place({ deleted: true }, { item: 'I9' }),
    place({ folder: 'F1', rating: 3, order: 'filename' }),
    place({ tags: ['x'], tags_any: ['p', 'q'], untagged: false }),
    place({ folder: 'none', untagged: true }, { smartId: 'S2' }),
    place({ preview: 'failed' }),
    place({ created_after: '2026-01-01T00:00:00Z' }, { item: 'I1', full: true }),
  ]
  for (const p of cases) assert.deepEqual(roundTrip(p), p, toUrl(p))
})

test('the root and old links land in Unfiled', () => {
  assert.equal(fromUrl('/', '').query.view, 'unfiled')
  assert.equal(fromUrl('/unfiled', '').query.view, 'unfiled')
  assert.equal(fromUrl('/nonsense', '').query.view, null)
  assert.equal(fromUrl('/all', '?full=1').full, false) // full needs an item
})

test('opening layers pushes; closing a pushed layer goes back', () => {
  const grid = place({ folder: 'F1' })
  const sheet = place({ folder: 'F1' }, { item: 'I1' })
  const full = place({ folder: 'F1' }, { item: 'I1', full: true })
  const mobile = { mobile: true, curPushed: false }
  assert.equal(layerOf(sheet, true), 'sheet')
  assert.equal(layerOf(sheet, false), 'view')
  assert.equal(historyAction(grid, sheet, mobile), 'push')
  assert.equal(historyAction(sheet, full, mobile), 'push')
  assert.equal(historyAction(full, sheet, { mobile: true, curPushed: true }), 'back')
  assert.equal(historyAction(sheet, grid, { mobile: true, curPushed: true }), 'back')
  // opened from a link (not pushed): closing just replaces
  assert.equal(historyAction(sheet, grid, mobile), 'replace')
  // swiping to the next item in full screen
  const nextFull = place({ folder: 'F1' }, { item: 'I2', full: true })
  assert.equal(historyAction(full, nextFull, { mobile: true, curPushed: true }), 'replace')
})

test('desktop selection changes replace; new views push', () => {
  const desk = { mobile: false, curPushed: false }
  const a = place({ folder: 'F1' }, { item: 'I1' })
  const b = place({ folder: 'F1' }, { item: 'I2' })
  assert.equal(historyAction(a, b, desk), 'replace')
  assert.equal(historyAction(a, place({ folder: 'F2' }), desk), 'push')
  assert.equal(historyAction(a, a, desk), 'none')
  // choosing a folder from the phone menu replaces the menu's entry
  assert.equal(
    historyAction(place({}), place({ folder: 'F2' }), { mobile: true, curPushed: true, curIsMenu: true }),
    'replace',
  )
})
