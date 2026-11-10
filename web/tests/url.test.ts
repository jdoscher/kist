// URL <-> place mapping and Back behaviour. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, type Query } from '../src/api.ts'
import { fromUrl, historyAction, layerOf, toUrl, type Place } from '../src/url.ts'
import { ITEM_KINDS } from '../src/api.ts'

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

test('Settings has its own URLs, and sections replace each other in history', () => {
  assert.equal(toUrl(place({}, { settings: 'tokens' })), '/settings/tokens')
  assert.equal(toUrl(place({ folder: 'F1' }, { settings: 'ai', item: 'I1' })), '/settings/ai')
  assert.equal(fromUrl('/settings/archive', '').settings, 'archive')
  assert.equal(fromUrl('/settings', '').settings, 'tokens')
  assert.equal(fromUrl('/settings/bogus', '').settings, 'tokens')
  const lib = place({ folder: 'F1' })
  const tokens = place({ folder: 'F1' }, { settings: 'tokens' })
  const ai = place({ folder: 'F1' }, { settings: 'ai' })
  assert.equal(historyAction(lib, tokens, { mobile: false, curPushed: false }), 'push')
  assert.equal(historyAction(tokens, ai, { mobile: false, curPushed: false }), 'replace')
  // from the phone menu, Settings takes the menu's entry
  assert.equal(historyAction(lib, tokens, { mobile: true, curPushed: true, curIsMenu: true }), 'replace')
})

test('the fixity filter round-trips; unknown params are ignored', () => {
  const p = place({ fixity: 'failed' })
  assert.equal(toUrl(p), '/all?fixity=failed')
  assert.deepEqual(roundTrip(p), p)
  const s = fromUrl('/all', '?flavour=brown%20boots&q=x')
  assert.equal(s.query.q, 'x')
  assert.equal(toUrl(s), '/all?q=x')
  assert.equal(fromUrl('/', '?fixity=nope').query.fixity, null)
})

test('Milestone 2 searches have URLs and round-trip', () => {
  const hybrid = place({ q: 'brown boots', semantic: 'brown boots' })
  assert.equal(toUrl(hybrid), '/all?q=brown+boots&semantic=brown+boots')
  assert.deepEqual(roundTrip(hybrid), hybrid)
  const like = place({ similar: 'I9' }, { item: 'I2' })
  assert.equal(toUrl(like), '/all?similar=I9&item=I2')
  assert.deepEqual(roundTrip(like), like)
  const colour = place({ color: '#2f4f3a', color_tol: 45 })
  assert.equal(toUrl(colour), '/all?color=%232f4f3a&tol=45')
  assert.deepEqual(roundTrip(colour), colour)
  // the default tolerance stays out of the URL
  assert.equal(toUrl(place({ color: '#2f4f3a' })), '/all?color=%232f4f3a')
  for (const q of [{ text: 'zero trust' }, { person: 'P1' }, { image: 'img1' }, { semantic: 'dog', folder: 'F1' }])
    assert.deepEqual(roundTrip(place(q)), place(q))
  // bad values are dropped, tolerance clamped
  assert.equal(fromUrl('/all', '?color=red').query.color, null)
  assert.equal(fromUrl('/all', '?color=%23ABCDEF&tol=400').query.color_tol, 100)
  assert.equal(fromUrl('/all', '?color=%23ABCDEF').query.color, '#abcdef')
})

test('Organise and the new Settings sections have their own paths', () => {
  for (const organise of ['duplicates', 'stacks', 'collections', 'people'] as const) {
    const p = place({}, { organise })
    assert.equal(toUrl(p), `/organise/${organise}`)
    assert.deepEqual(roundTrip(p), { ...p, query: { ...emptyQuery } })
  }
  assert.equal(fromUrl('/organise/nope', '').organise, 'duplicates')
  assert.equal(toUrl(place({}, { settings: 'vocabulary' })), '/settings/vocabulary')
  assert.equal(fromUrl('/settings/cleanup', '').settings, 'cleanup')
  // moving between Organise sections replaces; entering it pushes
  const lib = place({})
  const dups = place({}, { organise: 'duplicates' })
  const people = place({}, { organise: 'people' })
  assert.equal(historyAction(lib, dups, { mobile: false, curPushed: false }), 'push')
  assert.equal(historyAction(dups, people, { mobile: false, curPushed: false }), 'replace')
  // more like this, a colour or an image is a new place: Back returns
  const all = place({ q: 'boots' })
  assert.equal(historyAction(all, place({ similar: 'I9' }), { mobile: false, curPushed: false }), 'push')
  assert.equal(historyAction(all, place({ color: '#aabbcc' }), { mobile: false, curPushed: false }), 'push')
  assert.equal(historyAction(place({ image: 'a' }), place({ image: 'b' }), { mobile: false, curPushed: false }), 'push')
  // typing more words over it doesn't stack up entries
  assert.equal(historyAction(place({ image: 'a' }), place({ image: 'a', q: 'red' }), { mobile: false, curPushed: false }), 'replace')
  // clearing it is an ordinary filter change
  assert.equal(historyAction(place({ similar: 'I9' }), place({}), { mobile: false, curPushed: false }), 'replace')
  // leaving it for a person's items is a new place (Back returns)
  assert.equal(historyAction(people, place({ person: 'P1' }), { mobile: false, curPushed: false }), 'push')
})

test('M2 searches, M3 culling filters and the M1 filters round-trip together', () => {
  const all = place({
    q: 'boots',
    tags: ['shoes'],
    tags_any: ['red', 'brown'],
    folder: 'F1',
    type: 'image',
    rating: 3,
    order: 'filename',
    preview: 'ready',
    created_after: '2026-01-01',
    created_before: '2026-02-01',
    semantic: 'leather boots',
    color: '#2f4f3a',
    color_tol: 45,
    text: 'size 10',
    person: 'P1',
    flag: 'pick',
    label: 'green',
  })
  assert.deepEqual(roundTrip(all), all, toUrl(all))
  for (const q of [{ similar: 'I9', flag: 'reject' }, { image: 'img1', label: 'none' }, { flag: 'none', person: 'P2' }])
    assert.deepEqual(roundTrip(place(q)), place(q), toUrl(place(q)))
  // bad culling values are dropped
  assert.equal(fromUrl('/all', '?flag=maybe').query.flag, null)
  assert.equal(fromUrl('/all', '?label=pink').query.label, null)
})

test('every server kind is a type filter; unknown types are ignored', () => {
  for (const type of ITEM_KINDS) assert.deepEqual(roundTrip(place({ type })), place({ type }))
  assert.equal(fromUrl('/all', '?type=hologram').query.type, null)
})

test('boards, a board and triage have paths; Settings, Organise and screens exclude each other', () => {
  const cases: Place[] = [
    place({}, { screen: { kind: 'boards' } }),
    place({}, { screen: { kind: 'boards', trash: true } }),
    place({}, { screen: { kind: 'board', id: 'B1' } }),
    place({}, { screen: { kind: 'triage', view: 'all', folder: 'F1' } }),
    place({}, { screen: { kind: 'triage', view: 'unfiled', folder: null } }),
    place({}, { settings: 'templates' }),
    place({}, { settings: 'vocabulary' }),
    place({}, { organise: 'people' }),
  ]
  for (const p of cases) assert.deepEqual(roundTrip(p), p, toUrl(p))
  assert.equal(toUrl(place({}, { screen: { kind: 'board', id: 'a b' } })), '/board/a%20b')
  // a path is one thing: Settings wins, then Organise, then a screen
  assert.equal(toUrl(place({}, { settings: 'ai', organise: 'people', screen: { kind: 'boards' } })), '/settings/ai')
  assert.equal(toUrl(place({}, { organise: 'people', screen: { kind: 'boards' } })), '/organise/people')
})
