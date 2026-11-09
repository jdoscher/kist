// Upload queue state machine. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextQueued, uploadsReducer, type UploadEntry } from '../src/uploads.ts'

const add = (s: UploadEntry[], key: number, folderId: string | null) =>
  uploadsReducer(s, { type: 'add', entries: [{ key, job: key, name: `f${key}`, isUrl: false, folderId }] })

test('each file keeps the folder it was added in', () => {
  let s = add([], 1, 'F1')
  s = add(s, 2, 'F1')
  s = add(s, 3, 'F2') // the user switched folders mid-batch
  assert.deepEqual(s.map((e) => e.folderId), ['F1', 'F1', 'F2'])
})

test('one upload at a time, in order', () => {
  let s = add(add([], 1, null), 2, null)
  assert.equal(nextQueued(s)?.key, 1)
  s = uploadsReducer(s, { type: 'start', key: 1 })
  assert.equal(nextQueued(s), undefined)
  s = uploadsReducer(s, { type: 'finish', key: 1, created: true, itemId: 'I1', itemState: 'active', at: 0 })
  assert.equal(nextQueued(s)?.key, 2)
})

test('a duplicate that is in the trash says so, and can be restored', () => {
  let s = uploadsReducer(add([], 1, null), { type: 'start', key: 1 })
  s = uploadsReducer(s, { type: 'finish', key: 1, created: false, itemId: 'I1', itemState: 'trashed', at: 5 })
  assert.equal(s[0].status, 'inTrash')
  assert.equal(s[0].itemId, 'I1')
  s = uploadsReducer(s, { type: 'expire', now: 99999, afterMs: 4000 })
  assert.equal(s.length, 1, 'waits for the user')
  s = uploadsReducer(s, { type: 'restored', key: 1, at: 10 })
  assert.equal(s[0].status, 'done')
})

test('errors stay until retried or dismissed; finished ones expire', () => {
  let s = uploadsReducer(add(add([], 1, null), 2, null), { type: 'start', key: 1 })
  s = uploadsReducer(s, { type: 'fail', key: 1, error: 'network error' })
  s = uploadsReducer(s, { type: 'start', key: 2 })
  s = uploadsReducer(s, { type: 'finish', key: 2, created: true, itemId: 'I2', itemState: 'active', at: 0 })
  s = uploadsReducer(s, { type: 'expire', now: 5000, afterMs: 4000 })
  assert.deepEqual(s.map((e) => [e.key, e.status]), [[1, 'error']])
  s = uploadsReducer(s, { type: 'retry', key: 1 })
  assert.equal(s[0].status, 'queued')
  assert.equal(s[0].error, undefined)
  s = uploadsReducer(s, { type: 'start', key: 1 })
  s = uploadsReducer(s, { type: 'fail', key: 1, error: 'again' })
  s = uploadsReducer(s, { type: 'dismiss', key: 1 })
  assert.deepEqual(s, [])
})

test('events for the wrong state are ignored', () => {
  let s = add([], 1, null)
  s = uploadsReducer(s, { type: 'finish', key: 1, created: true, itemId: 'x', itemState: 'active', at: 0 })
  assert.equal(s[0].status, 'queued') // never started
  s = uploadsReducer(s, { type: 'dismiss', key: 1 })
  assert.equal(s.length, 1) // queued work isn't dropped by a stray dismiss
})
