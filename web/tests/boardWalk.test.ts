// Board walk order and the preview's info strip. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { boardWalkOrder, type WalkNode } from '../src/boardWalk.ts'
import { INFO_KEY, infoRows, readInfoPref, writeInfoPref } from '../src/previewInfo.ts'

const box = (item: string, x: number, y: number, w = 100, h = 100, type = 'item'): WalkNode => ({ id: `n-${item}-${x}-${y}`, type, x, y, w, h, item_id: type === 'item' ? item : undefined })

test('a grid reads in rows, left to right, whatever order the nodes are stored in', () => {
  const nodes = [box('f', 240, 240), box('a', 0, 0), box('c', 240, 0), box('b', 120, 0), box('e', 120, 240), box('d', 0, 240)]
  assert.deepEqual(boardWalkOrder(nodes), ['a', 'b', 'c', 'd', 'e', 'f'])
})

test('a ragged row (tops a little off) is still one row', () => {
  const nodes = [box('b', 120, 18), box('a', 0, 0), box('c', 240, -12), box('d', 0, 160)]
  assert.deepEqual(boardWalkOrder(nodes), ['a', 'b', 'c', 'd'])
})

test('tiles stacked beside a tall one read after it; a tile mostly below a row starts the next', () => {
  // two columns: a tall tile on the left, two short ones stacked on the right
  const nodes = [box('a', 0, 0, 100, 300), box('b', 120, 0, 100, 100), box('c', 120, 120, 100, 100)]
  // b and c both sit within a's height, so they share its row, top one first
  assert.deepEqual(boardWalkOrder(nodes), ['a', 'b', 'c'])
  const rows = [box('b', 0, 70), box('a', 0, 0)]
  // 30 px of 100 overlap: under half, so b is its own row
  assert.deepEqual(boardWalkOrder(rows), ['a', 'b'])
})

test('notes, text, shapes and group frames are skipped; an item placed twice counts once', () => {
  const nodes = [
    box('', 0, 0, 500, 500, 'group'),
    box('a', 10, 10),
    box('', 120, 10, 100, 100, 'note'),
    box('', 240, 10, 100, 100, 'text'),
    box('', 360, 10, 100, 100, 'shape'),
    box('b', 10, 200),
    box('a', 200, 200),
  ]
  assert.deepEqual(boardWalkOrder(nodes), ['a', 'b'])
})

test('an empty board has no walk', () => {
  assert.deepEqual(boardWalkOrder([]), [])
})

const base = { source_url: null, provenance: {}, tags: [] as string[], rating: 0, notes: '', caption: null }

test('info: the page it was saved from wins over the file URL, titled, as a link', () => {
  const rows = infoRows({ ...base, source_url: 'https://cdn.example.net/x.jpg', provenance: { page_url: 'https://www.example.com/post/1', page_title: 'A post' } })
  assert.deepEqual(rows[0], { label: 'Source', text: 'A post (example.com)', href: 'https://www.example.com/post/1' })
  const fileOnly = infoRows({ ...base, source_url: 'https://cdn.example.net/x.jpg' })
  assert.deepEqual(fileOnly[0], { label: 'Source', text: 'cdn.example.net', href: 'https://cdn.example.net/x.jpg' })
})

test('info: never links a non-http URL', () => {
  assert.deepEqual(infoRows({ ...base, source_url: 'javascript:alert(1)', provenance: { page_url: 'file:///etc/passwd' } }).map((r) => r.label), ['Rating'])
})

test('info: credit, tags, rating, caption and notes; empty ones left out, except the rating', () => {
  const rows = infoRows({ ...base, provenance: { creator: 'Ann', credit: 'The Met' }, tags: ['coat', 'wool'], rating: 3, notes: ' check the collar ', caption: 'A grey coat' })
  assert.deepEqual(
    rows.map((r) => [r.label, r.text]),
    [
      ['Credit', 'Ann / The Met'],
      ['Tags', 'coat, wool'],
      ['Rating', '★★★☆☆'],
      ['Caption', 'A grey coat'],
      ['Notes', 'check the collar'],
    ],
  )
  // the rating is always there, even unrated
  assert.deepEqual(infoRows(base), [{ label: 'Rating', text: '☆☆☆☆☆ not rated' }])
  assert.equal(infoRows({ ...base, rating: 5 })[0].text, '★★★★★')
  // an editor gets the Tags row even when there are none, for its tag field
  assert.deepEqual(infoRows(base, { editable: true }).map((r) => r.label), ['Tags', 'Rating'])
})

test('info pref: off by default, remembered, and safe without storage', () => {
  const m = new Map<string, string>()
  const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
  assert.equal(readInfoPref(store), false)
  writeInfoPref(true, store)
  assert.equal(m.get(INFO_KEY), '1')
  assert.equal(readInfoPref(store), true)
  writeInfoPref(false, store)
  assert.equal(readInfoPref(store), false)
  assert.equal(readInfoPref(null), false)
  const throwing = { getItem: () => { throw new Error('x') }, setItem: () => { throw new Error('x') } }
  assert.equal(readInfoPref(throwing), false)
  writeInfoPref(true, throwing)
})
