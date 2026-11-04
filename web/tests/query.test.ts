// Unfiled view rules. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Query } from '../src/api.ts'
import { isUnfiledView, mergeQuery, unfiledQuery } from '../src/query.ts'

const empty: Query = {
  q: '',
  tags: [],
  tags_any: [],
  untagged: false,
  folder: null,
  type: null,
  rating: 0,
  order: '-created',
  deleted: false,
  created_after: null,
  created_before: null,
}
const unfiled: Query = { ...empty, ...unfiledQuery }

test('Unfiled means no folder and no tags', () => {
  assert.equal(unfiled.folder, 'none')
  assert.equal(unfiled.untagged, true)
  assert.ok(isUnfiledView(unfiled))
  assert.ok(!isUnfiledView({ ...unfiled, untagged: false }))
  assert.ok(!isUnfiledView({ ...unfiled, deleted: true }))
})

test('leaving Unfiled drops its untagged condition', () => {
  assert.equal(mergeQuery(unfiled, { folder: 'f1' }).untagged, false)
  assert.equal(mergeQuery(unfiled, { deleted: true, folder: null }).untagged, false)
  const tagged = mergeQuery(unfiled, { tags: ['Cars'] })
  assert.equal(tagged.untagged, false) // not "untagged AND #Cars"
  assert.equal(tagged.folder, 'none') // no-folder items tagged Cars
  assert.equal(mergeQuery(unfiled, { tags_any: ['Cars', 'Trucks'] }).untagged, false)
})

test('staying in Unfiled keeps it, and explicit untagged is respected', () => {
  assert.ok(isUnfiledView(mergeQuery(unfiled, { q: 'watch' })))
  assert.ok(isUnfiledView(mergeQuery(unfiled, { type: 'image', order: 'filename' })))
  assert.equal(mergeQuery(unfiled, { folder: 'f1', untagged: true }).untagged, true)
  // outside Unfiled an untagged filter (smart folder) survives folder clicks
  const smart = { ...empty, untagged: true }
  assert.equal(mergeQuery(smart, { folder: 'f1' }).untagged, true)
})
