// Upload queue state machine. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextQueued, uploadsReducer, visibleUploads, type UploadEntry } from '../src/uploads.ts'

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

test('a big batch shows a few toasts and counts the rest', () => {
  let s: UploadEntry[] = []
  for (let k = 1; k <= 60; k++) s = add(s, k, null)
  for (let k = 1; k <= 20; k++) {
    s = uploadsReducer(s, { type: 'start', key: k })
    s = uploadsReducer(s, { type: 'finish', key: k, created: true, itemId: `i${k}`, itemState: 'active', at: k })
  }
  s = uploadsReducer(s, { type: 'start', key: 21 })
  s = uploadsReducer(s, { type: 'fail', key: 21, error: 'boom' })
  s = uploadsReducer(s, { type: 'start', key: 22 })
  const v = visibleUploads(s, 6)
  assert.equal(v.shown.length, 6)
  // the failed one and the one uploading always show, in queue order
  assert.ok(v.shown.some((e) => e.key === 21 && e.status === 'error'))
  assert.ok(v.shown.some((e) => e.key === 22 && e.status === 'uploading'))
  assert.deepEqual(v.shown.map((e) => e.key), [...v.shown.map((e) => e.key)].sort((a, b) => a - b))
  assert.equal(v.shown.length + v.hiddenQueued + v.hiddenDone, 60)
  // a small batch shows everything
  const small = visibleUploads(add(add([], 1, null), 2, null), 6)
  assert.equal(small.shown.length, 2)
  assert.equal(small.hiddenQueued + small.hiddenDone, 0)
})
