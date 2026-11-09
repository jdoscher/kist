// First-time-user messages and labels. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, type Query } from '../src/api.ts'
import { tileLabel } from '../src/format.ts'
import { emptyState, everywhere, viewTitle } from '../src/query.ts'
import { markOnce, onceSeen } from '../src/settings.ts'

const q = (over: Partial<Query> = {}): Query => ({ ...emptyQuery, ...over })
const opts = { libraryEmpty: false, mobile: false, title: 'Unfiled' }

test('an empty search names the view and offers Search everything', () => {
  const s = emptyState(q({ view: 'unfiled', q: 'dog' }), opts)
  assert.equal(s.text, 'Nothing in Unfiled matches “dog”.')
  assert.equal(s.action, 'everywhere')
  const all = emptyState(q({ q: 'dog' }), { ...opts, title: 'All items' })
  assert.equal(all.text, 'Nothing matches “dog”.')
  assert.equal(all.action, null)
})

test('only a truly empty library says how to add things', () => {
  assert.match(emptyState(q({ view: 'unfiled' }), { ...opts, libraryEmpty: true }).text, /Drop files/)
  const phone = emptyState(q(), { ...opts, libraryEmpty: true, mobile: true })
  assert.doesNotMatch(phone.text, /Drop/)
  assert.match(phone.text, /Upload/)
  const unfiled = emptyState(q({ view: 'unfiled' }), opts)
  assert.match(unfiled.text, /every item has a folder or a tag/)
  assert.equal(unfiled.action, 'all')
  assert.equal(emptyState(q({ deleted: true }), { ...opts, title: 'Trash' }).text, 'Trash is empty.')
})

test('Search everything keeps the search and drops the view', () => {
  const e = everywhere(q({ view: 'unfiled', folder: 'F', tags: ['a'], q: 'dog', type: 'image' }))
  assert.equal(e.view, null)
  assert.equal(e.folder, null)
  assert.deepEqual(e.tags, [])
  assert.equal(e.q, 'dog')
  assert.equal(e.type, 'image')
})

test('view titles', () => {
  assert.equal(viewTitle(q({ view: 'unfiled' })), 'Unfiled')
  assert.equal(viewTitle(q({ deleted: true })), 'Trash')
  assert.equal(viewTitle(q({ folder: 'F' }), { folder: () => 'Kitchen' }), 'Kitchen')
  assert.equal(viewTitle(q({ tags: ['a', 'b'] })), '#a #b')
  assert.equal(viewTitle(q(), { smart: 'Blue things' }), 'Blue things')
  assert.equal(viewTitle(q()), 'All items')
})

test('tile names say what the symbols show', () => {
  assert.equal(
    tileLabel({ filename: 'dog.png', kind: 'image', rating: 3, preview_status: 'ready', duration_s: null }),
    'dog.png, image, 3 stars',
  )
  assert.equal(
    tileLabel({ filename: 'clip.mp4', kind: 'video', rating: 1, preview_status: 'pending', duration_s: 75 }),
    'clip.mp4, preview pending, video, 1 star, 1:15',
  )
})

test('one-time flags', () => {
  const m = new Map<string, string>()
  const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
  assert.equal(onceSeen('visited', store), false)
  markOnce('visited', store)
  assert.equal(onceSeen('visited', store), true)
  assert.equal(onceSeen('visited', null), false)
})
