// Browser crash reports. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError } from '../src/api.ts'
import { createReporter, DEDUPE_MS, toReport, type ClientErrorReport } from '../src/reportError.ts'

function setup(token = 'tok') {
  const sent: ClientErrorReport[] = []
  let t = 1_000
  const report = createReporter({
    send: async (b) => void sent.push(b),
    now: () => t,
    token: () => token,
    context: () => ({ url: 'https://kist/x?item=1', user_agent: 'test' }),
  })
  return { sent, report, tick: (ms: number) => (t += ms) }
}

test('a render error is reported with its stack, the URL and the browser', () => {
  const { sent, report } = setup()
  const err = new TypeError("Cannot read properties of undefined (reading 'hostname')")
  assert.equal(report(toReport('render', err, '\n    at ItemMeta')), true)
  assert.equal(sent.length, 1)
  assert.equal(sent[0].kind, 'render')
  assert.equal(sent[0].message, "TypeError: Cannot read properties of undefined (reading 'hostname')")
  assert.match(sent[0].stack ?? '', /Component stack:\n {4}at ItemMeta/)
  assert.equal(sent[0].url, 'https://kist/x?item=1')
  assert.equal(sent[0].user_agent, 'test')
})

test('identical messages are sent once a minute', () => {
  const { sent, report, tick } = setup()
  report(toReport('error', new Error('boom')))
  report(toReport('error', new Error('boom')))
  report(toReport('error', new Error('other')))
  tick(DEDUPE_MS - 1)
  report(toReport('error', new Error('boom')))
  assert.equal(sent.length, 2)
  tick(1)
  report(toReport('error', new Error('boom')))
  assert.equal(sent.length, 3)
})

test('401s, signed-out sessions and browser noise are never reported', () => {
  const { sent, report } = setup()
  assert.equal(toReport('unhandledrejection', new ApiError(401, 'unauthorized')), null)
  assert.equal(toReport('unhandledrejection', { status: 401 }), null)
  assert.equal(toReport('error', 'ResizeObserver loop completed with undelivered notifications.'), null)
  assert.equal(report(null), false)
  const signedOut = setup('')
  assert.equal(signedOut.report(toReport('error', new Error('x'))), false)
  assert.equal(sent.length + signedOut.sent.length, 0)
})

test('long messages and stacks are cut to the server limits; odd values still report', () => {
  const big = new Error('x'.repeat(5000))
  big.stack = 'y'.repeat(20000)
  const r = toReport('error', big)!
  assert.equal(r.message.length, 2000)
  assert.equal(r.stack?.length, 10000)
  assert.equal(toReport('unhandledrejection', { code: 7 })?.message, '{"code":7}')
  assert.equal(toReport('unhandledrejection', 'plain')?.message, 'plain')
})

test('a failing send never throws', async () => {
  const report = createReporter({
    send: () => Promise.reject(new Error('offline')),
    token: () => 't',
  })
  assert.equal(report(toReport('error', new Error('a'))), true)
  const sync = createReporter({
    send: () => {
      throw new Error('sync')
    },
  })
  assert.equal(sync(toReport('error', new Error('b'))), true)
  await new Promise((r) => setTimeout(r, 5))
})
