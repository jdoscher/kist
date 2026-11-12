// The web side of the Kist 1.5 API contract. Run with `npm test`.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { api, ApiError, CLIENT_HEADERS, conflictItem, detailText, emptyQuery, type Item } from '../src/api.ts'
import { normalizeSaved, smartFolderQuery } from '../src/query.ts'
import { fromUrl, toUrl } from '../src/url.ts'
import {
  bannerText,
  clearDraftIf,
  describeError,
  discardDraft,
  draftKey,
  FOLDER_GONE,
  getDraft,
  isFolderGone,
  isRetryable,
  laterStamp,
  runEach,
  onDiscard,
  setDraft,
} from '../src/writes.ts'

test('server errors carry their request id as a ref', () => {
  assert.equal(describeError(new ApiError(500, 'Internal error', { requestId: '3f9a0c1d2e3f4a5b' })), 'Server error (500, ref 3f9a0c1d2e3f4a5b)')
  assert.equal(describeError(new ApiError(502, 'Bad gateway')), 'Server error (502)')
})

test('an unknown folder reads as a deleted folder', () => {
  const e = new ApiError(422, 'Unknown folder: 01ABC')
  assert.equal(describeError(e), FOLDER_GONE)
  assert.ok(isFolderGone(e))
  assert.ok(!isFolderGone(new ApiError(422, 'Tag names cannot contain commas')))
})

test('error bodies become readable text', () => {
  assert.equal(detailText('Unknown folder: x'), 'Unknown folder: x')
  assert.equal(detailText([{ loc: ['body', 'rating'], msg: 'Input should be less than or equal to 5' }]), 'Input should be less than or equal to 5')
  assert.equal(detailText({ message: 'Changed elsewhere since you opened it', item: {} }), 'Changed elsewhere since you opened it')
  assert.equal(detailText(undefined), null)
})

test('a 409 hands back the server copy of the item', () => {
  const item = { id: 'A', notes: 'theirs', modified_at: '2026-11-09T10:00:00Z' } as Item
  const e = new ApiError(409, 'Changed elsewhere since you opened it', {
    data: { detail: { message: 'Changed elsewhere since you opened it', item } },
  })
  assert.equal(conflictItem(e), item)
  assert.equal(conflictItem(new ApiError(500, 'x')), null)
  assert.equal(conflictItem(new Error('x')), null)
})

test('the banner tells a down database from a missing server', () => {
  assert.match(bannerText(500), /database/)
  assert.match(bannerText(503), /database/)
  assert.match(bannerText(0), /Can't reach/)
  assert.match(bannerText(undefined), /Can't reach/)
})

test('a finished save keeps text typed while it was in flight', () => {
  const key = draftKey('I1', 'notes')
  setDraft(key, 'abc')
  // the save of "abc" is in flight; you type on
  setDraft(key, 'abcdef')
  clearDraftIf(key, 'abc')
  assert.equal(getDraft(key), 'abcdef')
  clearDraftIf(key, 'abcdef')
  assert.equal(getDraft(key), undefined)
})

test('Use theirs drops the draft and tells the open field', () => {
  const key = draftKey('I2', 'notes')
  setDraft(key, 'mine')
  const seen: string[] = []
  const off = onDiscard(key, (v) => seen.push(v))
  discardDraft(key, 'theirs')
  assert.equal(getDraft(key), undefined)
  assert.deepEqual(seen, ['theirs'])
  off()
  discardDraft(key, 'again')
  assert.deepEqual(seen, ['theirs'])
})

test('base_modified_at is the newest copy this tab has', () => {
  assert.equal(laterStamp('2026-11-09T10:00:00Z', '2026-11-09T10:00:05Z'), '2026-11-09T10:00:05Z')
  assert.equal(laterStamp('2026-11-09T10:00:05Z', '2026-11-09T10:00:00Z'), '2026-11-09T10:00:05Z')
  assert.equal(laterStamp('2026-11-09T10:00:00Z', undefined), '2026-11-09T10:00:00Z')
  assert.equal(laterStamp(undefined, '2026-11-09T10:00:00Z'), '2026-11-09T10:00:00Z')
  // same millisecond, finer fraction wins
  assert.equal(laterStamp('2026-11-09T10:00:00.123Z', '2026-11-09T10:00:00.123456Z'), '2026-11-09T10:00:00.123456Z')
})

test('smart folders save and restore the preview filter and the trash', () => {
  const q = { ...emptyQuery, preview: 'failed' as const, deleted: true, q: 'cat' }
  const saved = smartFolderQuery(q)
  assert.equal(saved.preview, 'failed')
  assert.equal(saved.deleted, true)
  const back = normalizeSaved(saved)
  assert.equal(back.preview, 'failed')
  assert.equal(back.deleted, true)
  assert.equal(normalizeSaved({ q: 'x' }).deleted, false)
})

test('a smart folder over the trash keeps its own URL', () => {
  const place = { query: { ...emptyQuery, deleted: true, q: 'cat' }, smartId: 'S1', item: null, full: false }
  const url = toUrl(place)
  assert.equal(url, '/smart/S1?q=cat&trash=1')
  const [path, search] = url.split('?')
  const back = fromUrl(path, `?${search}`)
  assert.equal(back.smartId, 'S1')
  assert.equal(back.query.deleted, true)
  // plain trash is unchanged
  assert.equal(toUrl({ ...place, smartId: null }), '/trash?q=cat')
})

test('API requests say they come from the web app', async () => {
  assert.deepEqual(CLIENT_HEADERS, { 'X-Kist-Client': 'web' })
  const realFetch = globalThis.fetch
  let sent: Headers | undefined
  globalThis.fetch = (async (_url: RequestInfo | URL, init?: RequestInit) => {
    sent = new Headers(init?.headers)
    return new Response(JSON.stringify({ items: [], next_cursor: null, total: null }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
  try {
    const page = await api.listItems(emptyQuery, 'c2')
    assert.equal(page.total, null) // later pages carry no total
    assert.equal(sent?.get('X-Kist-Client'), 'web')
    assert.match(sent?.get('Authorization') ?? '', /^Bearer/)
  } finally {
    globalThis.fetch = realFetch
  }
})

test('a 204 answer resolves without parsing a body (removing a token entry)', async () => {
  const realFetch = globalThis.fetch
  let sent: { url: string; method?: string } | undefined
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    sent = { url: String(url), method: init?.method }
    return new Response(null, { status: 204 })
  }) as typeof fetch
  try {
    assert.equal(await api.removeTokenEntry('01T'), undefined)
    assert.deepEqual(sent, { url: '/api/tokens/01T/entry', method: 'DELETE' })
  } finally {
    globalThis.fetch = realFetch
  }
})

test('a smart folder saves every filter the server keeps (M1-M3), and nothing it would refuse', () => {
  const py = readFileSync(new URL('../../kist/schemas.py', import.meta.url), 'utf8')
  const body = py.slice(py.indexOf('class SmartFolderQuery'), py.indexOf('\nclass ', py.indexOf('class SmartFolderQuery') + 10))
  const serverFields = new Set([...body.matchAll(/^ {4}([a-z_]+): /gm)].map((m) => m[1]))
  const q = {
    ...emptyQuery,
    q: 'boots',
    semantic: 'leather',
    color: '#aabbcc',
    color_tol: 20,
    text: 'size',
    person: 'P1',
    flag: 'pick',
    label: 'red',
    type: 'audio',
  }
  const saved = smartFolderQuery(q)
  for (const k of Object.keys(saved)) assert.ok(serverFields.has(k), `smartFolderQuery sends ${k}, SmartFolderQuery has no such field`)
  for (const k of ['semantic', 'color', 'color_tol', 'text', 'person', 'flag', 'label', 'type'] as const)
    assert.equal(saved[k], q[k], k)
  // what the server sends back (unset fields as null) restores the same query
  const back = normalizeSaved({ ...saved, view: null } as Partial<typeof q>)
  for (const k of ['semantic', 'color', 'color_tol', 'text', 'person', 'flag', 'label', 'type'] as const) assert.equal(back[k], q[k], k)
  assert.equal(normalizeSaved({ flag: null, label: null } as Partial<typeof q>).flag, null)
})

test('only failures that could go differently offer Retry', async () => {
  // a protected item (409), a bad value (422), a missing item: the same every time
  assert.ok(!isRetryable(new ApiError(409, 'Protected: unprotect it first')))
  assert.ok(!isRetryable(new ApiError(422, 'Unknown folder: x')))
  assert.ok(!isRetryable(new ApiError(404, 'Not found')))
  // no answer, the server failing, a timeout or rate limit: try again
  assert.ok(isRetryable(new TypeError('Failed to fetch')))
  assert.ok(isRetryable(new ApiError(503, 'Unavailable')))
  assert.ok(isRetryable(new ApiError(429, 'Slow down')))
  const r = await runEach(['a', 'b'], async (id) => {
    if (id === 'a') throw new ApiError(409, 'Protected: unprotect it first')
    throw new TypeError('Failed to fetch')
  })
  assert.deepEqual(r.failed.map((f) => [f.id, f.retryable]), [['a', false], ['b', true]])
  assert.equal(r.failed[0].error, 'Protected: unprotect it first')
})
