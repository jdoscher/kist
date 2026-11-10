// Phone editing logic (W2.1). Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  completeFragment,
  currentFragment,
  folderChange,
  folderRows,
  moveDoneText,
  parseRecent,
  parseTagInput,
  pushRecent,
  tagSuggestions,
} from '../src/phoneEdit.ts'

const TAGS = [
  { name: 'sky', count: 4 },
  { name: 'skyline', count: 40 },
  { name: 'place:skye', count: 9 },
  { name: 'blue sky', count: 12 },
  { name: 'sketch', count: 100 },
  { name: 'cat', count: 1 },
]

test('typed tags: comma-separated, trimmed, no repeats (any case)', () => {
  assert.deepEqual(parseTagInput(' sky,  Sky , cat,, '), ['sky', 'cat'])
  assert.deepEqual(parseTagInput(''), [])
})

test('suggestions: starts-with by use, then contains, minus what the item has', () => {
  assert.deepEqual(tagSuggestions(TAGS, 'sk', []), ['sketch', 'skyline', 'place:skye', 'sky', 'blue sky'])
  // an exact name is what's typed already: not offered again
  assert.deepEqual(tagSuggestions(TAGS, 'sky', ['skyline']), ['place:skye', 'blue sky'])
  assert.deepEqual(tagSuggestions(TAGS, '', []), [])
  assert.equal(tagSuggestions(TAGS, 's', [], 2).length, 2)
})

test('suggestions follow the last comma and skip tags typed before it', () => {
  assert.equal(currentFragment('cat, sky'), 'sky')
  assert.deepEqual(tagSuggestions(TAGS, 'skyline, sky', []), ['place:skye', 'blue sky'])
  assert.equal(completeFragment('cat, sk', 'skyline'), 'cat, skyline')
  assert.equal(completeFragment('sk', 'skyline'), 'skyline')
})

test('Move makes the target the only folder; Also in adds it', () => {
  assert.deepEqual(folderChange('move', ['a', 'b'], 'c'), { add: ['c'], remove: ['a', 'b'] })
  assert.deepEqual(folderChange('move', ['a', 'c'], 'c'), { add: [], remove: ['a'] })
  assert.equal(folderChange('move', ['c'], 'c'), null)
  assert.deepEqual(folderChange('move', [], 'c'), { add: ['c'], remove: [] })
  assert.deepEqual(folderChange('also', ['a'], 'c'), { add: ['c'], remove: [] })
  assert.equal(folderChange('also', ['a', 'c'], 'c'), null)
})

test('move notices', () => {
  assert.equal(moveDoneText('move', 'Trips', 1), 'Moved to “Trips”.')
  assert.equal(moveDoneText('move', 'Trips', 0), 'Filed in “Trips”.')
  assert.equal(moveDoneText('also', 'Trips', 0), 'Also in “Trips”.')
})

test('recent folders: newest first, five at most, unknown ids dropped, bad JSON is empty', () => {
  assert.deepEqual(pushRecent(['a', 'b', 'c', 'd', 'e'], 'c'), ['c', 'a', 'b', 'd', 'e'])
  assert.deepEqual(pushRecent(['a', 'b', 'c', 'd', 'e'], 'f'), ['f', 'a', 'b', 'c', 'd'])
  assert.deepEqual(parseRecent('["a","x",3,"b"]', new Set(['a', 'b'])), ['a', 'b'])
  assert.deepEqual(parseRecent('{nope'), [])
  assert.deepEqual(parseRecent(null), [])
})

test('folder rows: the tree in order with depth; a search lists hits with their path', () => {
  const folders = [
    { id: '2', name: 'Work', parent_id: null, position: 1 },
    { id: '1', name: 'Home', parent_id: null, position: 0 },
    { id: '3', name: 'Kitchen', parent_id: '1', position: 0 },
    { id: '4', name: 'Work kitchen', parent_id: '2', position: 0 },
    { id: '5', name: 'Orphan', parent_id: 'gone', position: 0 },
  ]
  assert.deepEqual(
    folderRows(folders).map((r) => `${'-'.repeat(r.depth)}${r.name}`),
    ['Home', '-Kitchen', 'Orphan', 'Work', '-Work kitchen'],
  )
  assert.deepEqual(
    folderRows(folders, 'kitch').map((r) => [r.path, r.depth]),
    [
      ['Home / Kitchen', 0],
      ['Work / Work kitchen', 0],
    ],
  )
})

test('folder rows survive a parent cycle in bad data', () => {
  const rows = folderRows([
    { id: 'a', name: 'A', parent_id: 'b' },
    { id: 'b', name: 'B', parent_id: 'a' },
  ])
  // neither is reachable from the top: nothing hangs, nothing repeats
  assert.ok(rows.length <= 2)
})
