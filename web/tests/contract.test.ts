// The web side of the Kist 1.5 API contract. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError, conflictItem, detailText, emptyQuery, type Item } from '../src/api.ts'
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
  laterStamp,
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
