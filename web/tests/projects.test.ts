// F6 web logic (no React): project/field filters in requests and URLs,
// smart folders, screens, field values, pins and the REST contract.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, itemsParams, type Query } from '../src/api.ts'
import {
  fieldChipLabel,
  fieldKey,
  groupMembers,
  hasProjectFilters,
  isPinned,
  movePin,
  parseFieldInput,
  pinForPlace,
  projectFromSearch,
  projectsApi,
  projectSubtitle,
  searchPinUrl,
  withFieldFilter,
  type FieldDef,
  type Member,
} from '../src/projects.ts'
import { hasNarrowing, smartFolderQuery } from '../src/query.ts'
import { fromUrl, inLibraryFrame, toUrl } from '../src/url.ts'

const q = (patch: Partial<Query>): Query => ({ ...emptyQuery, ...patch })

test('field keys match the server', () => {
  assert.equal(fieldKey('Look number'), 'look_number')
  assert.equal(fieldKey('  Shot date / day '), 'shot_date_day')
  assert.equal(fieldKey('Édition'), 'dition')
})

test('project and field filters go to GET /api/items and survive the URL', () => {
  const query = q({ project: 'P1', custom_fields: { season: 'SS27', edition: '>=3' } })
  const p = itemsParams(query)
  assert.equal(p.get('project'), 'P1')
  assert.equal(p.get('field.season'), 'SS27')
  assert.equal(p.get('field.edition'), '>=3')
  const url = toUrl({ query, smartId: null, item: null, full: false })
  assert.match(url, /project=P1/)
  assert.match(url, /field\.season=SS27/)
  const [path, search] = url.split('?')
  const back = fromUrl(path, `?${search}`).query
  assert.equal(back.project, 'P1')
  assert.deepEqual(back.custom_fields, { season: 'SS27', edition: '>=3' })
  assert.ok(hasProjectFilters(back))
  assert.ok(hasNarrowing(back))
  // bad keys and empty values are dropped, never an error
  assert.deepEqual(projectFromSearch(new URLSearchParams('field.Bad Key=x&field.ok=&field.fine=1')), { custom_fields: { fine: '1' } })
  assert.ok(!hasProjectFilters(emptyQuery))
})

test('the timeline keeps the field filters', () => {
  const back = fromUrl('/timeline', '?field.season=AW27&project=P2').query
  assert.equal(back.project, 'P2')
  assert.deepEqual(back.custom_fields, { season: 'AW27' })
  const url = toUrl({ query: back, smartId: null, item: null, full: false, screen: { kind: 'timeline', group: 'month' } })
  assert.match(url, /^\/timeline\?.*field\.season=AW27/)
})

test('a smart folder saves them', () => {
  const saved = smartFolderQuery(q({ project: 'P1', custom_fields: { season: 'SS27' } }))
  assert.equal(saved.project, 'P1')
  assert.deepEqual(saved.custom_fields, { season: 'SS27' })
})

test('project screens have their own URLs in the library frame', () => {
  assert.equal(toUrl({ query: emptyQuery, smartId: null, item: null, full: false, screen: { kind: 'projects' } }), '/projects')
  assert.equal(toUrl({ query: emptyQuery, smartId: null, item: null, full: false, screen: { kind: 'projects', archived: true } }), '/projects?archived=1')
  assert.equal(toUrl({ query: emptyQuery, smartId: null, item: null, full: false, screen: { kind: 'project', id: 'A B' } }), '/project/A%20B')
  assert.deepEqual(fromUrl('/project/A%20B', '').screen, { kind: 'project', id: 'A B' })
  assert.deepEqual(fromUrl('/projects', '?archived=1').screen, { kind: 'projects', archived: true })
  assert.ok(inLibraryFrame({ kind: 'project', id: 'x' }))
  assert.ok(inLibraryFrame({ kind: 'projects' }))
})

test('field filter chips and edits', () => {
  assert.equal(fieldChipLabel('Edition', '>=3'), 'Edition ≥ 3')
  assert.equal(fieldChipLabel('Shot', '2026-01-01..2026-02-01'), 'Shot: 2026-01-01–2026-02-01')
  assert.equal(fieldChipLabel('Shot', '2026-01-01..'), 'Shot ≥ 2026-01-01')
  assert.equal(fieldChipLabel('Season', 'SS27|AW27'), 'Season: SS27 or AW27')
  assert.equal(fieldChipLabel('Paper', '*'), 'Paper: any')
  assert.equal(fieldChipLabel('Paper', 'none'), 'Paper: none')
  assert.deepEqual(withFieldFilter(q({ custom_fields: { a: '1' } }), 'b', '2'), { a: '1', b: '2' })
  assert.equal(withFieldFilter(q({ custom_fields: { a: '1' } }), 'a', null), null)
})

test('field values are checked like the server does', () => {
  const f = (type: FieldDef['type'], choices?: string[]): FieldDef => ({ id: 'f', name: 'F', key: 'f', type, choices })
  assert.deepEqual(parseFieldInput(f('number'), ' 3,5 '), { value: 3.5 })
  assert.ok('error' in parseFieldInput(f('number'), 'three'))
  assert.deepEqual(parseFieldInput(f('date'), '2026-11-12'), { value: '2026-11-12' })
  assert.ok('error' in parseFieldInput(f('date'), '12/11/2026'))
  assert.deepEqual(parseFieldInput(f('choice', ['SS27', 'AW27']), 'ss27'), { value: 'SS27' })
  assert.ok('error' in parseFieldInput(f('choice', ['SS27']), 'x'))
  assert.ok('error' in parseFieldInput(f('url'), 'example.com'))
  assert.deepEqual(parseFieldInput(f('text'), '   '), { value: null })
})

test('pins: what the current place pins, and reordering', () => {
  assert.deepEqual(pinForPlace({ query: q({ folder: 'F1' }), smartId: null }), { kind: 'folder', ref: 'F1' })
  assert.deepEqual(pinForPlace({ query: q({}), smartId: 'S1' }), { kind: 'smart', ref: 'S1' })
  assert.deepEqual(pinForPlace({ query: q({ tags: ['red'] }), smartId: null }), { kind: 'tag', ref: 'red' })
  assert.deepEqual(pinForPlace({ query: q({ person: 'P' }), smartId: null }), { kind: 'person', ref: 'P' })
  assert.deepEqual(pinForPlace({ query: q({}), smartId: null, screen: { kind: 'project', id: 'J' } }), { kind: 'project', ref: 'J' })
  assert.equal(pinForPlace({ query: q({}), smartId: null }), null)
  assert.equal(pinForPlace({ query: q({ folder: 'F1', deleted: true }), smartId: null }), null)
  const pins = [{ kind: 'folder', ref: 'a' }, { kind: 'tag', ref: 'b' }, { kind: 'search', ref: 'c' }]
  assert.deepEqual(movePin(pins, 2, 0).map((p) => p.ref), ['c', 'a', 'b'])
  assert.deepEqual(movePin(pins, 0, 2).map((p) => p.ref), ['b', 'c', 'a'])
  assert.equal(movePin(pins, 1, 1), pins)
  assert.ok(isPinned([{ kind: 'tag', ref: 'Red', name: 'Red' }], 'tag', 'red'))
  assert.ok(!isPinned([{ kind: 'folder', ref: 'A', name: 'A' }], 'folder', 'a'))
  assert.equal(searchPinUrl('?color=%23ff0000&item=X&full=1'), '/all?color=%23ff0000')
  assert.equal(searchPinUrl(''), '/all')
})

test('project page helpers', () => {
  assert.equal(projectSubtitle({ client: 'Acme', job_number: '', phase: 'Pitch' }), 'Acme · Pitch')
  const m = (kind: string, id: string): Member => ({ kind, id, name: id, missing: false })
  const grouped = groupMembers([m('board', 'b'), m('show', 's'), m('folder', 'f'), m('weird', 'w'), m('folder', 'g')])
  assert.deepEqual(grouped.map(([k, ms]) => [k, ms.length]), [['folder', 2], ['board', 1], ['show', 1], ['weird', 1]])
})

test('REST calls: methods, paths, bodies and the batch header', async () => {
  const calls: { url: string; init: RequestInit }[] = []
  const orig = globalThis.fetch
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  try {
    await projectsApi.list('archived')
    await projectsApi.setItemFields('I1', { f1: 3 }, 'web-batch')
    await projectsApi.bulkFields(['I1', 'I2'], { f1: null })
    await projectsApi.unpin('tag', 'red tag')
    await projectsApi.reorderPins([{ kind: 'folder', ref: 'F' }])
    await projectsApi.removeMember('P', 'folder', 'F')
  } finally {
    globalThis.fetch = orig
  }
  assert.equal(calls[0].url, '/api/projects?status=archived')
  assert.equal(calls[1].url, '/api/items/I1/fields')
  assert.equal(calls[1].init.method, 'PATCH')
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), { values: { f1: 3 } })
  assert.equal((calls[1].init.headers as Record<string, string>)['X-Kist-Batch'], 'web-batch')
  assert.equal(calls[2].url, '/api/fields/bulk')
  assert.deepEqual(JSON.parse(String(calls[2].init.body)), { item_ids: ['I1', 'I2'], values: { f1: null } })
  assert.equal(calls[3].url, '/api/pins?kind=tag&ref=red%20tag')
  assert.equal(calls[3].init.method, 'DELETE')
  assert.equal(calls[4].init.method, 'PUT')
  assert.equal(calls[5].url, '/api/projects/P/members/folder/F')
})
