// The notice list. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ACTION_MS, INFO_MS, addNotice, removeNotice, toNotice } from '../src/notices.ts'

test('info notices auto-dismiss, errors stay, actions get longer', () => {
  assert.equal(toNotice({ message: 'Saved' }, 1).timeoutMs, INFO_MS)
  assert.equal(toNotice({ message: 'Nope', tone: 'error' }, 2).timeoutMs, null)
  const undo = { label: 'Undo', run: () => {} }
  assert.equal(toNotice({ message: 'Trashed', action: undo }, 3).timeoutMs, ACTION_MS)
  assert.equal(toNotice({ message: 'x', timeoutMs: 100, tone: 'error' }, 4).timeoutMs, 100)
})

test('same key replaces; the list keeps the newest few', () => {
  let list = addNotice([], toNotice({ message: 'a', key: 'k' }, 1))
  list = addNotice(list, toNotice({ message: 'b' }, 2))
  list = addNotice(list, toNotice({ message: 'a again', key: 'k' }, 3))
  assert.deepEqual(list.map((n) => n.message), ['b', 'a again'])
  for (let i = 10; i < 20; i++) list = addNotice(list, toNotice({ message: `m${i}` }, i), 4)
  assert.deepEqual(list.map((n) => n.id), [16, 17, 18, 19])
})

test('remove by id or by key; no change returns the same list', () => {
  const list = [toNotice({ message: 'a', key: 'k' }, 1), toNotice({ message: 'b' }, 2)]
  assert.deepEqual(removeNotice(list, 'k').map((n) => n.id), [2])
  assert.deepEqual(removeNotice(list, 2).map((n) => n.id), [1])
  assert.equal(removeNotice(list, 99), list)
})

test('a notice can carry two actions (Keep mine / Use theirs)', () => {
  const keep = { label: 'Keep mine', run: () => {} }
  const theirs = { label: 'Use theirs', run: () => {} }
  const n = toNotice({ message: 'Changed elsewhere', tone: 'error', actions: [keep, theirs] }, 9)
  assert.deepEqual(n.actions.map((a) => a.label), ['Keep mine', 'Use theirs'])
  assert.equal(n.timeoutMs, null)
  const both = toNotice({ message: 'x', action: keep, actions: [theirs] }, 10)
  assert.deepEqual(both.actions.map((a) => a.label), ['Keep mine', 'Use theirs'])
  assert.deepEqual(toNotice({ message: 'y' }, 11).actions, [])
})
