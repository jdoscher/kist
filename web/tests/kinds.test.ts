// F1 utility collections: the pure helpers (src/kindsCore.ts) and the
// auto_kind filter riding in the Query. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  cleanKindFilter,
  kindFilterValue,
  kindsByPack,
  kindTitle,
  leavesList,
  learningText,
  parseKindFilter,
  sidebarKinds,
  strictnessText,
  type KindInfo,
  type KindsOverview,
} from '../src/kindsCore.ts'
import { cleanFilter, filterChipLabel, m45Filters, m45FromSearch } from '../src/m45/filters.ts'
import { viewTitle } from '../src/query.ts'
import { emptyQuery } from '../src/api.ts'

const kind = (id: string, extra: Partial<KindInfo> = {}): KindInfo => ({
  id,
  name: id,
  packs: ['base'],
  parent: null,
  enabled: true,
  enabled_override: null,
  opt_in: false,
  rule: null,
  clip: true,
  learnable: true,
  strictness: 0.5,
  threshold: 0.5,
  count: 30,
  confirmed: 0,
  rejected: 0,
  visible: true,
  learned: null,
  learn_min: 10,
  how: '',
  ...extra,
})

const overview = (kinds: KindInfo[]): KindsOverview => ({
  enabled: true,
  kinds,
  packs: [
    { id: 'base', name: 'Everyone', enabled: true },
    { id: 'architecture', name: 'Architecture & product', enabled: true },
    { id: 'film', name: 'Film & games', enabled: false },
  ],
  min_items: 20,
  low_res_px: 1200,
  print_size_source: 'pixels',
  pending_jobs: 0,
})

test('a kind filter value parses and prints the same way', () => {
  assert.deepEqual(parseKindFilter('drawings'), { id: 'drawings', not: false, mode: null })
  assert.deepEqual(parseKindFilter('-screenshots'), { id: 'screenshots', not: true, mode: null })
  assert.deepEqual(parseKindFilter('pencil:unsure'), { id: 'pencil', not: false, mode: 'unsure' })
  assert.equal(parseKindFilter('drawings:maybe'), null)
  assert.equal(parseKindFilter(''), null)
  for (const v of ['drawings', '-screenshots', 'ink:confirmed', 'low-res:rejected'])
    assert.equal(kindFilterValue(parseKindFilter(v)!), v)
})

test('URL values are cleaned like the server checks them', () => {
  assert.equal(cleanKindFilter(' Drawings '), 'drawings')
  assert.equal(cleanKindFilter('drawings,-screenshots'), 'drawings,-screenshots')
  assert.equal(cleanKindFilter('drawings;drop'), null)
  assert.equal(cleanFilter('auto_kind', 'ink:unsure'), 'ink:unsure')
  assert.equal(cleanFilter('auto_kind', '<x>'), null)
  // it rides in the Query like the other filters (URL, smart folders, chips)
  assert.deepEqual(m45FromSearch(new URLSearchParams('auto_kind=paintings')), { auto_kind: 'paintings' })
  assert.deepEqual(m45Filters({ ...emptyQuery, auto_kind: 'paintings' }), { auto_kind: 'paintings' })
})

test('titles and chips name the kind', () => {
  assert.equal(kindTitle('drawings'), 'Drawings')
  assert.equal(kindTitle('-screenshots'), 'Not Screenshots')
  assert.equal(kindTitle('pencil:unsure'), 'Pencil & graphite, to review')
  assert.equal(filterChipLabel('auto_kind', 'low-res'), 'Low-res')
  assert.equal(viewTitle({ ...emptyQuery, auto_kind: 'paintings' }), 'Paintings')
  // a folder keeps its own title; the kind is a chip
  assert.equal(viewTitle({ ...emptyQuery, auto_kind: 'paintings', folder: 'f1' }, { folder: () => 'Refs' }), 'Refs')
})

test('the sidebar shows full kinds, children under their parent, and the one on screen', () => {
  const o = overview([
    kind('screenshots'),
    kind('drawings'),
    kind('pencil', { parent: 'drawings', visible: false, count: 3 }),
    kind('ink', { parent: 'drawings' }),
    kind('paintings', { visible: false, count: 2 }),
    kind('runway', { enabled: false, visible: false, packs: ['fashion'] }),
  ])
  assert.deepEqual(
    sidebarKinds(o).map((k) => `${k.depth}:${k.id}`),
    ['0:screenshots', '0:drawings', '1:ink'],
  )
  assert.deepEqual(
    sidebarKinds(o, 'pencil').map((k) => `${k.depth}:${k.id}`),
    ['0:screenshots', '0:drawings', '1:pencil', '1:ink'],
  )
  assert.deepEqual(sidebarKinds({ ...o, enabled: false }), [])
})

test('Settings groups each kind under its first pack (server order)', () => {
  const o = overview([kind('screenshots'), kind('renders', { packs: ['architecture', 'film'] }), kind('hdris', { packs: ['film'] })])
  const groups = kindsByPack(o).map((g) => [g.pack.id, g.kinds.map((k) => k.id)])
  assert.deepEqual(groups, [
    ['base', ['screenshots']],
    ['architecture', ['renders']],
    ['film', ['hdris']],
  ])
})

test('strictness and learning read as words', () => {
  assert.equal(strictnessText(0), 'Very loose')
  assert.equal(strictnessText(0.5), 'Balanced')
  assert.equal(strictnessText(1), 'Very strict')
  assert.equal(learningText(kind('ink', { confirmed: 3, rejected: 4 })), 'Learns after 3 more corrections')
  assert.equal(learningText(kind('ink', { confirmed: 6, rejected: 3 })), 'Learns after 1 more correction')
  assert.equal(
    learningText(kind('ink', { learned: { corrections: 14, pos: 6, neg: 8, trained_at: '' } })),
    'Learned from 14 corrections',
  )
  assert.match(learningText(kind('photos', { learnable: false })), /Rule-based/)
})

test('the preview moves on when a correction takes the item out of the list', () => {
  assert.equal(leavesList(null, false), true) // Not this: gone from the kind
  assert.equal(leavesList(null, true), false)
  assert.equal(leavesList('unsure', true), true) // answered: out of the review queue
  assert.equal(leavesList('rejected', true), true)
  assert.equal(leavesList('rejected', false), false)
})
