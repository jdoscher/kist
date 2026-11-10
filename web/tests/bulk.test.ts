// Bulk notice wording (src/bulkText.ts). Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { bulkDoneText } from '../src/bulkText.ts'

test('bulk edits get a notice (shown with Undo); trash keeps its own', () => {
  assert.equal(bulkDoneText({ kind: 'addTags', tags: ['a', 'b c'] }, 3), 'Tagged 3 items #a #b c.')
  assert.equal(bulkDoneText({ kind: 'addTags', tags: ['x'] }, 1), 'Tagged 1 item #x.')
  assert.equal(bulkDoneText({ kind: 'addFolder', folderId: 'f1' }, 2, (id) => (id === 'f1' ? 'Moodboards' : undefined)), 'Added 2 items to “Moodboards”.')
  assert.equal(bulkDoneText({ kind: 'move', folderId: null }, 2), 'Took 2 items out of every folder.')
  assert.equal(bulkDoneText({ kind: 'protect', value: true }, 2), 'Protected 2 items.')
  assert.equal(bulkDoneText({ kind: 'flag', flag: 'reject' }, 4), 'Marked 4 items as rejected.')
  assert.equal(bulkDoneText({ kind: 'rating', rating: 1 }, 1), 'Rated 1 item 1 star.')
  assert.equal(bulkDoneText({ kind: 'trash' }, 2), null)
  assert.equal(bulkDoneText({ kind: 'restore' }, 2), null)
})
