// Failed, slow and repeated writes. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError } from '../src/api.ts'
import {
  clearDraft,
  describeError,
  draftKey,
  getDraft,
  hasDrafts,
  runEach,
  serialize,
  setDraft,
} from '../src/writes.ts'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

test('writes to one item reach the server in click order, even when slow', async () => {
  const server: number[] = []
  const send = (rating: number, delay: number) =>
    serialize('item-1', async () => {
      await sleep(delay)
      server.push(rating)
      return rating
    })
  // first click slow, second fast: without serializing, 3 would land last
  const answers = await Promise.all([send(3, 40), send(5, 1)])
  assert.deepEqual(server, [3, 5])
  assert.deepEqual(answers, [3, 5])
})

test('a failed write does not block the next one; other items run in parallel', async () => {
  const order: string[] = []
  const a = serialize('x', async () => {
    throw new Error('boom')
  })
  const b = serialize('x', async () => {
    order.push('x2')
    return 1
  })
  const c = serialize('y', async () => {
    order.push('y1')
    return 1
  })
  await assert.rejects(a)
  await Promise.all([b, c])
  assert.deepEqual(order.sort(), ['x2', 'y1'])
})

test('drafts survive until cleared', () => {
  const k = draftKey('I1', 'notes')
  assert.equal(getDraft(k), undefined)
  setDraft(k, 'half-written note')
  assert.ok(hasDrafts())
  assert.equal(getDraft(k), 'half-written note')
  clearDraft(k)
  assert.ok(!hasDrafts())
})

test('errors read as plain reasons', () => {
  assert.equal(describeError(new ApiError(0, 'x')), 'No connection to the server')
  assert.equal(describeError(new ApiError(408, 'x')), 'The server took too long')
  assert.equal(describeError(new ApiError(500, 'Internal Server Error')), 'Server error (500)')
  assert.equal(describeError(new ApiError(401, 'unauthorized')), 'Signed out: enter the token again')
  assert.equal(describeError(new ApiError(409, 'not in the trash')), 'not in the trash')
  assert.equal(describeError(new TypeError('Failed to fetch')), 'No connection to the server')
})

test('bulk runs carry on past failures and stop at sign-out', async () => {
  const r = await runEach(['a', 'b', 'c'], async (id) => {
    if (id === 'b') throw new ApiError(500, 'x')
    return id
  })
  assert.deepEqual(r.done, ['a', 'c'])
  assert.deepEqual(r.failed, [{ id: 'b', error: 'Server error (500)' }])

  let calls = 0
  const s = await runEach(['a', 'b', 'c', 'd'], async (id) => {
    calls++
    if (id === 'b') throw new ApiError(401, 'unauthorized')
    return id
  })
  assert.equal(calls, 2) // nothing sent after the 401
  assert.deepEqual(s.failed.map((f) => f.id), ['b', 'c', 'd'])
})
