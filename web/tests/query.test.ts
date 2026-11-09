// Unfiled view rules. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, type Query } from '../src/api.ts'
import { folderSubtree, isUnfiledView, mergeQuery, stillMatches, unfiledQuery } from '../src/query.ts'
import type { Item } from '../src/api.ts'

const empty: Query = emptyQuery
const unfiled: Query = { ...empty, ...unfiledQuery }

test('Unfiled is the server view, not a combination of filters', () => {
  assert.equal(unfiled.view, 'unfiled')
  assert.equal(unfiled.untagged, false)
  assert.equal(unfiled.folder, null)
  assert.ok(isUnfiledView(unfiled))
  assert.ok(!isUnfiledView({ ...unfiled, deleted: true }))
})

test('picking a folder, tag or Trash leaves Unfiled', () => {
  assert.equal(mergeQuery(unfiled, { folder: 'f1' }).view, null)
  assert.equal(mergeQuery(unfiled, { deleted: true, folder: null }).view, null)
  const tagged = mergeQuery(unfiled, { tags: ['Cars'] })
  assert.equal(tagged.view, null) // every item tagged Cars, not "untagged AND #Cars"
  assert.equal(tagged.folder, null)
  assert.equal(mergeQuery(unfiled, { tags_any: ['Cars', 'Trucks'] }).view, null)
})

test('searching and sorting stay in Unfiled; untagged is an ordinary filter', () => {
  assert.ok(isUnfiledView(mergeQuery(unfiled, { q: 'watch' })))
  assert.ok(isUnfiledView(mergeQuery(unfiled, { type: 'image', order: 'filename' })))
  // a smart folder's untagged filter survives folder clicks
  const smart = { ...empty, untagged: true }
  assert.equal(mergeQuery(smart, { folder: 'f1' }).untagged, true)
})

const it = (p: Partial<Item>): Item =>
  ({ id: 'I', kind: 'image', state: 'active', tags: [], folder_ids: [], rating: 0,
     preview_status: 'ready', ...p }) as Item

test('an edited item leaves views it no longer belongs to', () => {
  assert.ok(stillMatches(it({}), unfiled))
  assert.ok(!stillMatches(it({ tags: ['cars'] }), unfiled)) // tagging clears the inbox
  assert.ok(!stillMatches(it({ state: 'trashed' }), empty))
  assert.ok(stillMatches(it({ state: 'trashed' }), { ...empty, deleted: true }))
  assert.ok(!stillMatches(it({ state: 'active' }), { ...empty, deleted: true })) // restored
  assert.ok(!stillMatches(it({ tags: ['a'] }), { ...empty, tags: ['A', 'b'] }))
  assert.ok(stillMatches(it({ tags: ['a', 'B'] }), { ...empty, tags: ['A', 'b'] }))
  assert.ok(!stillMatches(it({ rating: 2 }), { ...empty, rating: 3 }))
  assert.ok(!stillMatches(it({ preview_status: 'ready' }), { ...empty, preview: 'failed' }))
  assert.ok(stillMatches(it({ tags: ['x'] }), { ...empty, q: 'anything' })) // can't tell: keep
})

test('folder views include subfolders', () => {
  const folders = [
    { id: 'A', parent_id: null }, { id: 'B', parent_id: 'A' }, { id: 'C', parent_id: 'B' },
    { id: 'D', parent_id: null },
  ]
  const sub = folderSubtree('A', folders)
  assert.deepEqual([...sub].sort(), ['A', 'B', 'C'])
  const inA = { ...empty, folder: 'A' }
  assert.ok(stillMatches(it({ folder_ids: ['C'] }), inA, sub))
  assert.ok(!stillMatches(it({ folder_ids: ['D'] }), inA, sub)) // moved out
})
