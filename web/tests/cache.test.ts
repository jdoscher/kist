// In-place list patching (the write rule). Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, type Item, type Query } from '../src/api.ts'
import { patchPages, type ItemsPages } from '../src/cache.ts'

const item = (id: string, over: Partial<Item> = {}): Item =>
  ({
    id,
    filename: `${id}.png`,
    kind: 'image',
    tags: [],
    folder_ids: [],
    rating: 0,
    state: 'active',
    preview_status: 'ready',
    ...over,
  }) as Item

const pagesOf = (...pages: Item[][]): ItemsPages => ({
  pages: pages.map((items, k) => ({ items, next_cursor: null, total: k === 0 ? 10 : null })),
  pageParams: pages.map(() => ''),
})

const q = (over: Partial<Query> = {}): Query => ({ ...emptyQuery, ...over })

test('an edit that keeps the item in view replaces it in place', () => {
  const data = pagesOf([item('a'), item('b')], [item('c')])
  const out = patchPages(data, q(), new Map([['c', item('c', { rating: 4 })]]), new Set(), [])
  assert.equal(out.pages[1].items[0].rating, 4)
  assert.equal(out.pages[0].total, 10)
  assert.equal(out.pages[0].items[0], data.pages[0].items[0]) // untouched items keep identity
})

test('items that leave the view are dropped and the total shrinks', () => {
  const data = pagesOf([item('a'), item('b')], [item('c')])
  const trashed = item('a', { state: 'trashed' })
  const out = patchPages(data, q(), new Map([['a', trashed]]), new Set(['c']), [])
  assert.deepEqual(out.pages.flatMap((p) => p.items.map((i) => i.id)), ['b'])
  assert.equal(out.pages[0].total, 8)
})

test('folder views include subfolders', () => {
  const folders = [
    { id: 'top', parent_id: null },
    { id: 'sub', parent_id: 'top' },
  ]
  const data = pagesOf([item('a', { folder_ids: ['top'] })])
  const moved = item('a', { folder_ids: ['sub'] })
  const out = patchPages(data, q({ folder: 'top' }), new Map([['a', moved]]), new Set(), folders)
  assert.deepEqual(out.pages[0].items, [moved])
  const gone = patchPages(data, q({ folder: 'top' }), new Map([['a', item('a')]]), new Set(), folders)
  assert.equal(gone.pages[0].items.length, 0)
})

test('lists the edit does not touch are returned unchanged', () => {
  const data = pagesOf([item('a')])
  assert.equal(patchPages(data, q(), new Map([['z', item('z')]]), new Set(), []), data)
})
