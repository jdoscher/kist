// Unfiled view rules. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, type Query } from '../src/api.ts'
import { isUnfiledView, mergeQuery, unfiledQuery } from '../src/query.ts'

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
