// Item history and undo wording (src/history.ts). Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ItemEvent } from '../src/api.ts'
import {
  actorName,
  batchSummary,
  batchUndoable,
  canRevert,
  describeEvent,
  eventWhat,
  linkLabel,
  reasonText,
  timeAgo,
  undoReport,
  whoText,
} from '../src/history.ts'

const ev = (kind: string, before: unknown, after: unknown, actor = 'Agent'): ItemEvent => ({
  id: 1,
  item_id: 'I1',
  at: '2026-11-09T10:00:00Z',
  actor,
  source: 'mcp',
  batch_id: 'B1',
  kind,
  before: before as Record<string, unknown> | null,
  after: after as Record<string, unknown> | null,
})

test('events read as plain sentences', () => {
  assert.equal(describeEvent(ev('tags', { tags: ['x'] }, { tags: ['a', 'b', 'x'] })), 'Agent added tags #a #b')
  assert.equal(describeEvent(ev('tags', { tags: ['a', 'x'] }, { tags: ['x'] })), 'Agent removed tag #a')
  assert.equal(
    eventWhat(ev('tags', { tags: ['a'] }, { tags: ['b'] })),
    'added tag #b and removed tag #a',
  )
  // a case-only change isn't an add and a remove
  assert.equal(eventWhat(ev('tags', { tags: ['Shoes'] }, { tags: ['shoes', 'new'] })), 'added tag #new')
  assert.equal(describeEvent(ev('create', null, {}, 'owner')), 'You added it')
  assert.equal(describeEvent(ev('trash', { state: 'active' }, { state: 'trashed' }, 'worker')), 'Kist moved it to the Trash')
  assert.equal(eventWhat(ev('restore', {}, {})), 'restored it from the Trash')
  assert.equal(eventWhat(ev('protect', { protected: false }, { protected: true })), 'protected it')
  assert.equal(eventWhat(ev('protect', { protected: true }, { protected: false })), 'removed its protection')
  assert.equal(eventWhat(ev('purge', {}, null)), 'deleted it for good')
})

test('edits name each field', () => {
  assert.equal(
    eventWhat(ev('edit', { filename: 'a.png' }, { filename: 'b.png' })),
    'renamed it from “a.png” to “b.png”',
  )
  assert.equal(eventWhat(ev('edit', { rating: 0 }, { rating: 3 })), 'rated it 3 stars')
  assert.equal(eventWhat(ev('edit', { rating: 3 }, { rating: 0 })), 'cleared the rating')
  assert.equal(eventWhat(ev('edit', { rating: 3, notes: '' }, { rating: 1, notes: 'hi' })), 'rated it 1 star, added notes')
  assert.equal(eventWhat(ev('edit', { created_at: 'x' }, { created_at: 'y' })), 'changed the date added')
})

test('folder and link events use names when known', () => {
  const names = { folder: (id: string) => ({ F1: 'Shoes', F2: 'Boots' })[id], item: (id: string) => ({ I2: 'crop.png' })[id] }
  assert.equal(
    eventWhat(ev('folders', { folder_ids: ['F1'] }, { folder_ids: ['F2'] }), names),
    'added it to “Boots” and removed it from “Shoes”',
  )
  assert.equal(eventWhat(ev('folders', { folder_ids: [] }, { folder_ids: ['F9'] }), names), 'added it to a folder that no longer exists')
  assert.equal(
    eventWhat(ev('link', null, { link: { item_id: 'I2', kind: 'derived_from', direction: 'out' } }), names),
    'linked it to “crop.png” (derived from)',
  )
  assert.equal(
    eventWhat(ev('unlink', { link: { item_id: 'I3', kind: 'related', direction: 'out' } }, null), names),
    'removed its link to another item (related)',
  )
})

test('who: actors and sources', () => {
  assert.equal(actorName('owner'), 'You')
  // signed in as someone else: the owner is named, and "You" is that token
  assert.equal(actorName('owner', 'Patty iPad'), 'The owner')
  assert.equal(actorName('Patty iPad', 'Patty iPad'), 'You')
  assert.equal(batchSummary({ actor: 'Claude', source: 'mcp', count: 2, kinds: ['tags'] }, 'Claude'), 'You via MCP tagged 2 items')
  assert.equal(actorName('importer'), 'The inbox importer')
  assert.equal(whoText('Claude', 'mcp'), 'Claude via MCP')
  assert.equal(whoText('owner', 'web'), 'You in the web app')
  assert.equal(whoText('cli', 'cli'), 'The command line')
  assert.equal(whoText('worker', 'worker'), 'Kist')
})

test('batches summarize in one line', () => {
  assert.equal(batchSummary({ actor: 'Claude', source: 'mcp', count: 12, kinds: ['tags'] }), 'Claude via MCP tagged 12 items')
  assert.equal(batchSummary({ actor: 'owner', source: 'web', count: 1, kinds: ['trash'] }), 'You in the web app trashed 1 item')
  assert.equal(
    batchSummary({ actor: 'Claude', source: 'mcp', count: 5, kinds: ['folders', 'tags'] }),
    'Claude via MCP made 5 changes (folders, tags)',
  )
  assert.equal(batchSummary({ actor: 'worker', source: 'worker', count: 3, kinds: ['purge'] }), 'Kist permanently deleted 3 items')
  assert.ok(!batchUndoable({ kinds: ['purge'] }))
  assert.ok(batchUndoable({ kinds: ['purge', 'tags'] }))
  assert.ok(!canRevert({ kind: 'purge' }))
  assert.ok(canRevert({ kind: 'tags' }))
})

test('undo results and conflicts read plainly', () => {
  assert.deepEqual(undoReport({ undone: 12, skipped: [], batch_id: 'X' }), { message: 'Undid 12 changes.', tone: 'info' })
  const r = undoReport(
    {
      undone: 1,
      skipped: [
        { item_id: 'I1', event_id: 4, reason: 'changed later: tag:a' },
        { item_id: 'I2', event_id: 5, reason: 'changed later: tag:a' },
      ],
      batch_id: 'X',
    },
    'Undid',
  )
  assert.equal(r.tone, 'error')
  assert.equal(r.message, 'Undid 1 change. 2 skipped: it was changed again later (#a) (and 1 more).')
  assert.equal(
    undoReport({ undone: 0, skipped: [{ item_id: 'I', event_id: 1, reason: 'the item no longer exists' }], batch_id: 'X' }, 'Reverted').message,
    'Nothing to undo: 1 skipped: the item no longer exists.',
  )
  assert.equal(
    reasonText('changed later: folder:F1, state', { folder: () => 'Shoes' }),
    'it was changed again later (folder “Shoes”, Trash state)',
  )
})

test('link labels depend on direction', () => {
  assert.equal(linkLabel('derived_from', 'out'), 'Derived from')
  assert.equal(linkLabel('derived_from', 'in'), 'Source of')
  assert.equal(linkLabel('version_of', 'in'), 'Has version')
  assert.equal(linkLabel('not_duplicate', 'in'), 'Not a duplicate of')
})

test('times read as ago', () => {
  const now = Date.parse('2026-11-09T12:00:00Z')
  assert.equal(timeAgo('2026-11-09T11:59:40Z', now), 'just now')
  assert.equal(timeAgo('2026-11-09T11:58:00Z', now), '2 min ago')
  assert.equal(timeAgo('2026-11-09T09:00:00Z', now), '3 h ago')
  assert.equal(timeAgo('2026-11-08T10:00:00Z', now), 'yesterday')
  assert.equal(timeAgo('2026-11-05T12:00:00Z', now), '4 days ago')
  assert.equal(timeAgo('nonsense', now), '')
})
