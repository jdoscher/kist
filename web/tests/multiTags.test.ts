// The info panel's tag editor for a multi-selection. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { addTargets, removeLabel, shareText, sharedByAll, tagTally } from '../src/multiTags.ts'

const ITEMS = [
  { id: 'a', tags: ['street', 'sky'] },
  { id: 'b', tags: ['street', 'night'] },
  { id: 'c', tags: ['street', 'sky', 'sky'] },
  { id: 'd', tags: [] },
]

test('tally: every tag on any item, most-shared first, then by name; ids of those items', () => {
  assert.deepEqual(tagTally(ITEMS), [
    { tag: 'street', count: 3, ids: ['a', 'b', 'c'] },
    { tag: 'sky', count: 2, ids: ['a', 'c'] }, // a repeat on one item counts once
    { tag: 'night', count: 1, ids: ['b'] },
  ])
  assert.deepEqual(tagTally([]), [])
})

test('share text only on tags some of them miss', () => {
  assert.equal(shareText(3, 5), '3/5')
  assert.equal(shareText(5, 5), null)
})

test('remove label says how many items lose it', () => {
  assert.equal(removeLabel({ tag: 'sky', count: 4, ids: [] }, 4), 'Remove tag sky from all 4 items')
  assert.equal(removeLabel({ tag: 'sky', count: 2, ids: [] }, 4), 'Remove tag sky from the 2 of 4 items that have it')
})

test('adding goes only to items missing one of the tags (any case)', () => {
  assert.deepEqual(addTargets(ITEMS, ['Street']), ['d'])
  assert.deepEqual(addTargets(ITEMS, ['street', 'sky']), ['b', 'd'])
  assert.deepEqual(addTargets(ITEMS, ['new']), ['a', 'b', 'c', 'd'])
})

test('tags on every item are not suggested again', () => {
  const t = tagTally(ITEMS.slice(0, 3))
  assert.deepEqual(sharedByAll(t, 3), ['street'])
})
