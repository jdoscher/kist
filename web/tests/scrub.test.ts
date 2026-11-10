// Date scrubber maths (M6 W1.1). Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, itemsParams } from '../src/api.ts'
import {
  bucketAt,
  bucketParams,
  fitBucket,
  geometry,
  loadAction,
  monthLabel,
  offsetOfPosition,
  perItemHeight,
  positionAtOffset,
  railPosition,
  rowAtOffset,
  scrubModel,
  scrubSort,
  sliderKey,
  untilOverlap,
  windowStart,
  yearTicks,
  type Bucket,
} from '../src/scrub.ts'

// newest first: 2024-06 (300), 2024-05 (100), 2024-01 (50), 2023-12 (200), 2021-03 (10), no date (40)
const B: Bucket[] = [
  { key: '2024-06', count: 300, cursor: 'c0' },
  { key: '2024-05', count: 100, cursor: 'c1' },
  { key: '2024-01', count: 50, cursor: 'c2' },
  { key: '2023-12', count: 200, cursor: 'c3' },
  { key: '2021-03', count: 10, cursor: 'c4' },
  { key: null, count: 40, cursor: 'c5' },
]
const m = scrubModel({ buckets: B })

test('only date sorts outside ranked searches get a scrubber', () => {
  assert.deepEqual(scrubSort({ ...emptyQuery, order: '-created' }), { sort: 'created', order: 'desc' })
  assert.deepEqual(scrubSort({ ...emptyQuery, order: 'created' }), { sort: 'created', order: 'asc' })
  assert.deepEqual(scrubSort({ ...emptyQuery, order: '-modified' }), { sort: 'modified', order: 'desc' })
  assert.deepEqual(scrubSort({ ...emptyQuery, order: '-taken' }), { sort: 'taken', order: 'desc' })
  assert.deepEqual(scrubSort({ ...emptyQuery, order: 'taken' }), { sort: 'taken', order: 'asc' })
  for (const order of ['filename', '-size', '-rating']) assert.equal(scrubSort({ ...emptyQuery, order }), null)
  assert.equal(scrubSort({ ...emptyQuery, semantic: 'boots' }), null)
  assert.equal(scrubSort({ ...emptyQuery, similar: 'x' }), null)
  assert.equal(scrubSort({ ...emptyQuery, color: '#ff0000' }), null)
  assert.equal(scrubSort({ ...emptyQuery, text: 'invoice' }), null)
})

test('bucket params keep every list filter and swap order for sort + order', () => {
  const q = { ...emptyQuery, order: '-taken', tags: ['a', 'b'], folder: 'f1', rating: 3, q: 'boots' }
  const p = bucketParams(itemsParams(q, 'cur', 120), scrubSort(q)!)
  assert.equal(p.get('sort'), 'taken')
  assert.equal(p.get('order'), 'desc')
  assert.equal(p.get('tags'), 'a,b')
  assert.equal(p.get('folder'), 'f1')
  assert.equal(p.get('rating'), '3')
  assert.equal(p.get('q'), 'boots')
  assert.equal(p.get('stacks'), 'collapse')
  assert.equal(p.has('cursor'), false)
  assert.equal(p.has('limit'), false)
})

test('month labels', () => {
  assert.equal(monthLabel('2024-06'), 'June 2024')
  assert.equal(monthLabel('1999-12'), 'December 1999')
  assert.equal(monthLabel('2024-01'), 'January 2024')
  assert.equal(monthLabel(null), 'No date')
  assert.equal(monthLabel('weird'), 'weird')
})

test('list positions map to buckets', () => {
  assert.deepEqual(m.starts, [0, 300, 400, 450, 650, 660])
  assert.equal(m.total, 700)
  assert.equal(bucketAt(m, 0), 0)
  assert.equal(bucketAt(m, 299.9), 0)
  assert.equal(bucketAt(m, 300), 1)
  assert.equal(bucketAt(m, 699), 5)
  assert.equal(bucketAt(m, 5000), 5)
  assert.equal(bucketAt(m, -3), 0)
  assert.equal(bucketAt(scrubModel({ buckets: [] }), 3), -1)
})

test('the window starts at the bucket whose cursor fetched the first page', () => {
  assert.equal(windowStart(m, ''), 0)
  assert.equal(windowStart(m, undefined), 0)
  assert.equal(windowStart(m, 'c3'), 3)
  assert.equal(windowStart(m, 'unknown'), 0)
})

test('year ticks, thinned', () => {
  const t = yearTicks(m)
  assert.deepEqual(t.map((x) => x.label), ['2024', '2023', '2021', 'No date'])
  assert.deepEqual(t.map((x) => x.bucket), [0, 3, 4, 5])
  assert.ok(Math.abs(t[1].at - 450 / 700) < 1e-9)
  // 2021 is 10 items above "No date": a big min gap drops the later tick
  assert.deepEqual(yearTicks(m, 0.05).map((x) => x.label), ['2024', '2023', '2021'])
})

test('slider keys: months, years, ends', () => {
  assert.equal(sliderKey(m, 0, 'ArrowDown'), 1)
  assert.equal(sliderKey(m, 0, 'ArrowUp'), 0)
  assert.equal(sliderKey(m, 5, 'ArrowRight'), 5)
  assert.equal(sliderKey(m, 0, 'PageDown'), 3) // first 2023 bucket
  assert.equal(sliderKey(m, 3, 'PageDown'), 4)
  assert.equal(sliderKey(m, 2, 'PageUp'), 0) // start of 2024
  assert.equal(sliderKey(m, 3, 'PageUp'), 0) // already at 2023's start: 2024's
  assert.equal(sliderKey(m, 0, 'End'), 5)
  assert.equal(sliderKey(m, 4, 'Home'), 0)
  assert.equal(sliderKey(m, 2, 'x'), null)
})

test('per-item height from full rows, with a fallback', () => {
  const rows = [
    { height: 200, tiles: [1, 2, 3, 4] },
    { height: 180, tiles: [1, 2, 3] },
    { height: 200, tiles: [1] }, // last, partial: ignored
  ]
  assert.equal(perItemHeight(rows, 8, { size: 200, avail: 800 }), (208 + 188) / 7)
  // too few: one row of square tiles across the width
  assert.equal(perItemHeight([], 8, { size: 200, avail: 832 }), 208 / 4)
})

test('honest heights: the months before the window and the rest after it', () => {
  // window at 2023-12 (list position 450), 120 loaded in 1000 px, 10 px per item
  const g = geometry(m, 3, 120, 1000, 10, true)
  assert.equal(g.top, 4500)
  assert.equal(g.bottom, (700 - 450 - 120) * 10)
  assert.equal(geometry(m, 3, 120, 1000, 10, false).bottom, 0)
  assert.equal(geometry(m, 0, 700, 7000, 10, false).top, 0)
})

test('offsets and list positions round-trip through the loaded rows', () => {
  const g = geometry(m, 3, 12, 300, 10, true)
  // 3 loaded rows of 4 items, 100 px each, starting after the top space
  const starts = [4500, 4600, 4700]
  const first = [0, 4, 8]
  const heights = [100, 100, 100]
  assert.equal(positionAtOffset(g, 0, starts, first, heights), 0)
  assert.equal(positionAtOffset(g, 3000, starts, first, heights), 300) // 2024-05 starts here
  assert.equal(positionAtOffset(g, 4500, starts, first, heights), 450)
  assert.equal(positionAtOffset(g, 4650, starts, first, heights), 456)
  assert.equal(positionAtOffset(g, 4800 + 50, starts, first, heights), 450 + 12 + 5)
  assert.equal(offsetOfPosition(g, 300, starts, first), 3000)
  assert.equal(offsetOfPosition(g, 455, starts, first), 4600)
  assert.equal(offsetOfPosition(g, 462, starts, first), 4800)
  assert.equal(rowAtOffset(starts, 10), 0)
  assert.equal(rowAtOffset([], 10), -1)
})

test('what to load for where the viewport is', () => {
  const vh = 800
  // window at 2023-12, loaded 4500..7500
  const g = geometry(m, 3, 100, 3000, 10, true)
  assert.deepEqual(loadAction(m, g, 3, 4600, vh, true), { kind: 'none' })
  assert.deepEqual(loadAction(m, g, 3, 6000, vh, true), { kind: 'next' })
  assert.deepEqual(loadAction(m, g, 3, 4400, vh, true), { kind: 'prepend' })
  // far above: a new window at the month on screen (2024-05 at 3000-3999)
  assert.deepEqual(loadAction(m, g, 3, 3200, 400, true), { kind: 'jump', bucket: 1 })
  // just above it: the month before the window, prepended
  assert.deepEqual(loadAction(m, g, 3, 3200, vh, true), { kind: 'prepend' })
  // far below the loaded end: the month there (position 550 + 170: no date)
  assert.deepEqual(loadAction(m, g, 3, 7500 + 1700, vh, true), { kind: 'jump', bucket: 5 })
  // the list's end loaded: nothing more below
  assert.deepEqual(loadAction(m, geometry(m, 3, 250, 2500, 10, false), 3, 6500, vh, false), { kind: 'none' })
  // window at the start: no prepend
  assert.deepEqual(loadAction(m, geometry(m, 0, 120, 3000, 10, true), 0, 0, vh, true), { kind: 'none' })
})

test('a page from the month before stops where the window begins', () => {
  const have = new Set(['c', 'd'])
  assert.deepEqual(untilOverlap([{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }], have), { items: [{ id: 'a' }, { id: 'b' }], met: true })
  assert.deepEqual(untilOverlap([{ id: 'a' }], have), { items: [{ id: 'a' }], met: false })
})

test('a jump near the end starts early enough to fill the screen', () => {
  assert.equal(fitBucket(m, 1, 100), 1)
  assert.equal(fitBucket(m, 0, 100), 0)
  // the last two buckets hold 50 items: 120 to show starts in 2023-12
  assert.equal(fitBucket(m, 5, 120), 3)
  assert.equal(fitBucket(m, 4, 30), 4)
  assert.equal(fitBucket(m, 5, 10000), 0)
})

test('the rail thumb reaches the end over the last screenful', () => {
  assert.equal(railPosition(100, 700, 0, 5000, 800), 100)
  assert.equal(railPosition(600, 700, 4200, 5000, 800), 600)
  assert.ok(Math.abs(railPosition(600, 700, 4600, 5000, 800) - 650) < 0.01)
  assert.equal(railPosition(600, 700, 5000, 5000, 800), 699.999)
  assert.equal(bucketAt(m, railPosition(600, 700, 5000, 5000, 800)), 5)
})
