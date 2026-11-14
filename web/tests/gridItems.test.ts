// P4: the grid's slim list (GET /api/items?fields=grid) and the full item
// fetched on open (src/api.ts GridItem, src/cache.ts listIsNewer).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { api, emptyQuery, type GridItem, type Item } from '../src/api.ts'
import { listIsNewer } from '../src/cache.ts'

async function urlsOf(fn: () => Promise<unknown>): Promise<string[]> {
  const realFetch = globalThis.fetch
  const urls: string[] = []
  globalThis.fetch = (async (url: RequestInfo | URL) => {
    urls.push(String(url))
    return new Response(JSON.stringify({ items: [], next_cursor: null, total: 0, id: 'x' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
  try {
    await fn()
  } finally {
    globalThis.fetch = realFetch
  }
  return urls
}

test('the grid list and the scrubber window ask for slim items', async () => {
  const [list] = await urlsOf(() => api.listItems({ ...emptyQuery, view: 'unfiled' }, 'c2'))
  const p = new URL(list, 'http://x').searchParams
  assert.equal(p.get('fields'), 'grid')
  // the unfiled filter keeps its own parameter (why it isn't view=grid)
  assert.equal(p.get('view'), 'unfiled')
  assert.equal(p.get('cursor'), 'c2')
  const [from] = await urlsOf(() => api.listItemsFrom(emptyQuery, 'c9', 300))
  assert.equal(new URL(from, 'http://x').searchParams.get('fields'), 'grid')
})

test('opening an item asks for the whole item', async () => {
  const [one] = await urlsOf(() => api.getItem('01ABC'))
  assert.equal(one, '/api/items/01ABC')
})

test('the web GridItem names exactly the fields the server sends (schemas.GridItemOut)', () => {
  const py = readFileSync(new URL('../../kist/schemas.py', import.meta.url), 'utf8')
  const block = py.slice(py.indexOf('class GridItemOut(BaseModel):'), py.indexOf('    @classmethod', py.indexOf('class GridItemOut(BaseModel):')))
  const server = [...block.matchAll(/^ {4}(\w+): /gm)].map((m) => m[1]).sort()
  const ts = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')
  const pick = ts.slice(ts.indexOf('export type GridItem = Pick<'), ts.indexOf('>', ts.indexOf('export type GridItem = Pick<')))
  const web = [...pick.matchAll(/\| '(\w+)'/g)].map((m) => m[1]).sort()
  assert.ok(server.length > 20)
  assert.deepEqual(web, server)
})

const slim = (o: Partial<GridItem>): GridItem =>
  ({ id: 'a', preview_rev: 1, preview_status: 'ready', modified_at: '2026-11-14T10:00:00-08:00', ...o }) as GridItem
const full = (o: Partial<Item>): Item =>
  ({ id: 'a', preview_rev: 1, preview_status: 'ready', modified_at: '2026-11-14T10:00:00-08:00', ...o }) as Item

test('the full copy is fetched again when the list knows a newer version', () => {
  assert.equal(listIsNewer(slim({}), full({})), false)
  // an edit from elsewhere (same instant written in another zone is not newer)
  assert.equal(listIsNewer(slim({ modified_at: '2026-11-14T10:00:01-08:00' }), full({})), true)
  assert.equal(listIsNewer(slim({ modified_at: '2026-11-14T18:00:00Z' }), full({})), false)
  // the full copy is newer than the list (this tab's edit): keep it
  assert.equal(listIsNewer(slim({}), full({ modified_at: '2026-11-14T11:00:00-08:00' })), false)
  // a new preview, or one that finished
  assert.equal(listIsNewer(slim({ preview_rev: 2 }), full({})), true)
  assert.equal(listIsNewer(slim({}), full({ preview_status: 'pending' })), true)
  assert.equal(listIsNewer(slim({ preview_status: 'pending' }), full({})), false)
})

test('the info panel, phone sheet and preview read the full item', () => {
  const src = (f: string) => readFileSync(new URL(`../src/components/${f}`, import.meta.url), 'utf8')
  assert.match(src('DetailPanel.tsx'), /useShownItem\(onlyId, items\)/)
  assert.match(src('DetailSheet.tsx'), /useShownItem\(selection\.size === 1/)
  assert.match(src('Preview.tsx'), /useShownItem\(previewId, items\)/)
  const data = readFileSync(new URL('../src/data.ts', import.meta.url), 'utf8')
  // never the list's slim copy in place of the full one
  assert.doesNotMatch(data, /return found \?\? fresh/)
})
