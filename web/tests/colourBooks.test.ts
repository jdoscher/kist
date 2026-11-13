// F5 colour books: the code helpers, the API calls (a 204 delete, the
// import form), and that a code search survives the URL.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { cbApi, deText, deWords, isBookRef, parseRef, refFor, swatchLabel } from '../src/colourBooks.ts'
import { emptyQuery } from '../src/api.ts'
import { fromUrl, toUrl } from '../src/url.ts'

test('refs', () => {
  assert.equal(refFor('ral', 'RAL 7016'), 'book:ral/RAL 7016')
  assert.deepEqual(parseRef('book:ral/RAL 7016'), { book: 'ral', code: 'RAL 7016' })
  assert.deepEqual(parseRef('book:tcx/19-4052 TCX'), { book: 'tcx', code: '19-4052 TCX' })
  assert.equal(parseRef('#2f4f3a'), null)
  assert.ok(isBookRef('book:ral/RAL 7016'))
  assert.ok(!isBookRef('#2f4f3a'))
  assert.ok(!isBookRef(null))
})

test('ΔE in numbers and words', () => {
  assert.equal(deText(0.42), 'ΔE 0.4')
  assert.equal(deText(12.6), 'ΔE 13')
  assert.equal(deWords(0.5), 'same to the eye')
  assert.equal(deWords(1.5), 'very close')
  assert.equal(deWords(3), 'close')
  assert.equal(deWords(7), 'near')
  assert.equal(deWords(25), 'closest in the book')
  assert.equal(swatchLabel({ code: 'RAL 7016', name: 'Anthracite grey' }), 'RAL 7016 Anthracite grey')
  assert.equal(swatchLabel({ code: 'Brand red', name: '' }), 'Brand red')
})

async function capture(run: () => Promise<unknown>, status = 200, body: unknown = {}) {
  const realFetch = globalThis.fetch
  let sent: { url: string; method?: string; body?: unknown } | undefined
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    sent = { url: String(url), method: init?.method, body: init?.body }
    return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  try {
    return { result: await run(), sent: sent! }
  } finally {
    globalThis.fetch = realFetch
  }
}

test('deleting a book is a 204 that resolves without a body', async () => {
  const { result, sent } = await capture(() => cbApi.remove('ral'), 204)
  assert.equal(result, undefined)
  assert.deepEqual({ url: sent.url, method: sent.method }, { url: '/api/colourbooks/ral', method: 'DELETE' })
})

test('import sends the file and the name as a form; lookup and codes paths', async () => {
  const file = new File(['name,code,hex\nA,B,#112233\n'], 'house.csv', { type: 'text/csv' })
  const { sent } = await capture(() => cbApi.import(file, ' House '), 201, { id: 'house' })
  assert.equal(sent.url, '/api/colourbooks')
  assert.equal(sent.method, 'POST')
  const form = sent.body as FormData
  assert.equal((form.get('file') as File).name, 'house.csv')
  assert.equal(form.get('name'), 'House')
  assert.equal((await capture(() => cbApi.lookup('RAL 7016'), 200, [])).sent.url, '/api/colourbooks/lookup?q=RAL+7016&limit=12')
  assert.equal((await capture(() => cbApi.paletteCodes('01X'), 200, [])).sent.url, '/api/items/01X/palette/codes')
  const p = await capture(() => cbApi.patch('ral', { enabled: false }), 200, {})
  assert.equal(p.sent.method, 'PATCH')
  assert.equal(p.sent.body, '{"enabled":false}')
})

test('a code search survives the address bar', () => {
  const q = { ...emptyQuery, color: 'book:ral/RAL 7016' }
  const url = toUrl({ query: q, smartId: null, item: null, full: false })
  const back = fromUrl(new URL(url, 'http://x').pathname, new URL(url, 'http://x').search)
  assert.equal(back.query.color, 'book:ral/RAL 7016')
  // and junk is still dropped
  const junk = fromUrl('/', '?color=javascript:alert(1)')
  assert.equal(junk.query.color, null)
})

test('a code search is titled by its code', async () => {
  const { searchTitle } = await import('../src/search.ts')
  assert.equal(searchTitle({ ...emptyQuery, color: 'book:ral/RAL 7016' }), 'Colour RAL 7016')
  assert.equal(searchTitle({ ...emptyQuery, color: '#abcdef' }), 'Colour #abcdef')
})
