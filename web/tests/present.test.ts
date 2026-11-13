// Present mode (2.0 F2): captions, navigation, compare joins, preloading,
// timers, the offline plan, the phone's pairing link. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  captionFor,
  clampIndex,
  comparePairs,
  DEFAULT_OPTIONS,
  formatClock,
  initialOptions,
  joinWithNext,
  kenBurnsAllowed,
  nextFlag,
  notesFor,
  offlinePlan,
  parseRemoteLocation,
  phoneState,
  preloadUrls,
  showItemIds,
  slideSeconds,
  splitCompare,
  storedOptions,
  typedJump,
  updateItem,
  type PresentState,
  type Slide,
  type SlideItem,
} from '../src/present.ts'

const item = (id: string, extra: Partial<SlideItem> = {}): SlideItem => ({
  id,
  filename: `IMG_${id}_final_FINAL.png`,
  ext: 'png',
  mime: 'image/png',
  kind: 'image',
  width: 4000,
  height: 3000,
  size_bytes: 10e6,
  preview_rev: 1,
  src: `/api/items/${id}/file?present=1`,
  src_kind: 'original',
  thumb: `/api/items/${id}/thumb?size=1024&v=1&present=1`,
  title: '',
  credit: '',
  license: '',
  notes: '',
  caption: '',
  flag: null,
  rating: 0,
  stack_id: null,
  motion_ok: true,
  low_res: false,
  ...extra,
})
const single = (id: string, extra: Partial<SlideItem> = {}): Slide => ({ kind: 'single', items: [item(id, extra)] })

test('Ken Burns is off by default and never moves drawings or compare slides', () => {
  assert.equal(DEFAULT_OPTIONS.ken_burns, false)
  assert.equal(kenBurnsAllowed(single('a'), DEFAULT_OPTIONS), false)
  assert.equal(kenBurnsAllowed(single('a'), { ken_burns: true }), true)
  assert.equal(kenBurnsAllowed(single('a', { motion_ok: false }), { ken_burns: true }), false)
  assert.equal(kenBurnsAllowed({ kind: 'compare', items: [item('a'), item('b')] }, { ken_burns: true }), false)
  // a remembered setup never brings Ken Burns back on its own
  const stored = storedOptions({ ...DEFAULT_OPTIONS, ken_burns: true, surround: 'black' })
  assert.equal(initialOptions(stored).ken_burns, false)
  assert.equal(initialOptions(stored).surround, 'black')
  assert.deepEqual(initialOptions('not json'), DEFAULT_OPTIONS)
})

test('captions show title and credit, file names only when asked', () => {
  const it = item('a', { title: 'Villa Savoye', credit: 'Photo: A. Person', license: 'CC BY 4.0' })
  assert.deepEqual(captionFor(it, { captions: true, filenames: false }), {
    title: 'Villa Savoye',
    credit: 'Photo: A. Person · CC BY 4.0',
    filename: '',
  })
  assert.equal(captionFor(item('b'), { captions: true, filenames: false }), null)
  assert.equal(captionFor(it, { captions: false, filenames: false }), null)
  assert.equal(captionFor(it, { captions: false, filenames: true })?.filename, 'IMG_a_final_FINAL.png')
})

test('presenter notes come from notes, then the caption', () => {
  assert.equal(notesFor(single('a', { notes: 'Ask about the ramp', caption: 'A white house' })), 'Ask about the ramp')
  assert.equal(notesFor(single('a', { caption: 'A white house' })), 'A white house')
  const both: Slide = { kind: 'compare', items: [item('a', { notes: 'v3' }), item('b', { notes: 'v4' })] }
  assert.equal(notesFor(both), 'Left: v3\n\nRight: v4')
  assert.equal(notesFor(undefined), '')
})

test('navigation, jumps and preloading', () => {
  assert.equal(clampIndex(-3, 5), 0)
  assert.equal(clampIndex(9, 5), 4)
  assert.equal(clampIndex(2, 0), 0)
  let s = typedJump('', '1')
  s = typedJump(s.buffer, '4')
  assert.deepEqual(typedJump(s.buffer, 'Enter'), { buffer: '', go: 13 })
  assert.deepEqual(typedJump('12', 'ArrowRight'), { buffer: '', go: null })
  const slides = ['a', 'b', 'c', 'd', 'e'].map((x) => single(x))
  // the next two, then the one before
  assert.deepEqual(preloadUrls(slides, 2), ['/api/items/d/file?present=1', '/api/items/e/file?present=1', '/api/items/b/file?present=1'])
  assert.deepEqual(preloadUrls(slides, 4), ['/api/items/d/file?present=1'])
})

test('compare slides: join with the next, split again, saved as pairs', () => {
  const slides = ['a', 'b', 'c'].map((x) => single(x))
  const joined = joinWithNext(slides, 0)
  assert.equal(joined.length, 2)
  assert.deepEqual(comparePairs(joined), [['a', 'b']])
  assert.deepEqual(showItemIds(joined), ['a', 'b', 'c'])
  assert.equal(joinWithNext(joined, 0), joined) // a pair stays a pair
  assert.deepEqual(splitCompare(joined, 0).map((x) => x.items[0].id), ['a', 'b', 'c'])
  assert.equal(joinWithNext(slides, 2), slides) // nothing after the last
})

test('a pick toggles off with the same key, and updates every copy', () => {
  assert.equal(nextFlag(null, 'pick'), 'pick')
  assert.equal(nextFlag('pick', 'pick'), null)
  assert.equal(nextFlag('reject', 'pick'), 'pick')
  const slides = [single('a'), { kind: 'compare', items: [item('b'), item('a')] } as Slide]
  const out = updateItem(slides, 'a', { flag: 'pick' })
  assert.equal(out[0].items[0].flag, 'pick')
  assert.equal(out[1].items[1].flag, 'pick')
  assert.equal(out[1].items[0].flag, null)
})

test('timers', () => {
  assert.equal(slideSeconds({ advance_seconds: 0, gesture_seconds: null }), 0)
  assert.equal(slideSeconds({ advance_seconds: 10, gesture_seconds: null }), 10)
  assert.equal(slideSeconds({ advance_seconds: 10, gesture_seconds: 60 }), 60)
  assert.equal(formatClock(0), '0:00')
  assert.equal(formatClock(65_000), '1:05')
  assert.equal(formatClock(3_729_000), '1:02:09')
})

test('the offline plan keeps whole slides in order until the cap, never videos', () => {
  const slides = [single('a'), single('b'), single('c', { src_kind: 'display' }), single('v', { kind: 'video' })]
  const plan = offlinePlan(slides, 21e6)
  assert.deepEqual(plan.urls, ['/api/items/a/file?present=1', '/api/items/b/file?present=1'])
  assert.equal(plan.slides, 2)
  const all = offlinePlan(slides, 1e9)
  assert.equal(all.urls.length, 3)
  assert.equal(all.bytes, 22e6)
})

test('the phone link carries the token in the fragment only', () => {
  const id = '01JC0Z7J5YQ6V2WJ3F4P8T9K2M'
  assert.deepEqual(parseRemoteLocation(`/present/remote/${id}`, '#abcdefghijklmnopqrstuvwxyz_-0123'), {
    id,
    token: 'abcdefghijklmnopqrstuvwxyz_-0123',
  })
  assert.equal(parseRemoteLocation(`/present/remote/${id}`, ''), null)
  assert.equal(parseRemoteLocation('/present/remote/../../api', '#abcdefghijklmnopqrstuvwxyz'), null)
  assert.equal(parseRemoteLocation(`/present/remote/${id}`, '#<script>'), null)
})

test('the phone gets text and ids only, never URLs', () => {
  const state: PresentState = {
    name: 'Crit',
    index: 0,
    count: 2,
    slide: { kind: 'compare', items: [item('a', { title: 'A', flag: 'pick' }), item('b', { title: 'B' })] },
    next: single('c'),
    notes: 'n',
    startedAt: 0,
    slideEndsAt: null,
    paused: false,
    blank: false,
    options: DEFAULT_OPTIONS,
  }
  const p = phoneState(state)
  assert.deepEqual(p, { index: 0, count: 2, item_id: 'a', next_item_id: 'c', title: 'A / B', credit: '', notes: 'n', flag: 'pick,-', compare: true, blank: false })
  assert.ok(!JSON.stringify(p).includes('/api/'))
})
