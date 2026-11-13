// F3 guest proofing: grouping, texts, query params, the /feedback URL and
// the API helpers' requests. Run with `npm test`.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { emptyQuery } from '../src/api.ts'
import {
  acceptedText,
  countsText,
  feedbackParams,
  groupByItem,
  namesText,
  proofApi,
  roundText,
  tileBadgeText,
  versionNote,
  type CommentRow,
  type FeedbackRow,
  type MarkRow,
} from '../src/proofing.ts'
import { fromUrl, inLibraryFrame, toUrl } from '../src/url.ts'

const base = {
  share_id: 'S1',
  share_title: 'Mood',
  round: 1,
  guest_id: 'g1',
  guest_name: 'Ann',
  item_id: 'I1',
  item_filename: 'a.png',
  item_state: 'active' as const,
  item_version: 1,
  seen_version_changed: false,
  has_newer_version: false,
  at: '2026-11-12T10:00:00Z',
  unseen: false,
  accepted: false,
}
const mark = (o: Partial<MarkRow>): MarkRow => ({ ...base, id: 'm', kind: 'mark', verdict: null, heart: false, accepted_as: null, ...o })
const comment = (o: Partial<CommentRow>): CommentRow => ({ ...base, id: 'c', kind: 'comment', text: 'hi', annotation_id: null, ...o })

test('groupByItem: one group per share, round and item, newest first', () => {
  const rows: FeedbackRow[] = [
    mark({ id: 'm1', verdict: 'pick', heart: true }),
    mark({ id: 'm2', guest_id: 'g2', guest_name: 'Bo', verdict: 'reject', at: '2026-11-12T11:00:00Z', unseen: true }),
    mark({ id: 'm3', guest_id: 'g3', guest_name: 'Cy', heart: true, accepted: true }),
    comment({ id: 'c2', at: '2026-11-12T09:30:00Z' }),
    comment({ id: 'c1', at: '2026-11-12T09:00:00Z' }),
    mark({ id: 'm4', item_id: 'I2', verdict: 'pick', at: '2026-11-12T12:00:00Z' }),
    mark({ id: 'm5', round: 2, verdict: 'pick', item_version: 2, has_newer_version: true }),
  ]
  const groups = groupByItem(rows)
  assert.deepEqual(groups.map((g) => g.key), ['S1:1:I2', 'S1:1:I1', 'S1:2:I1'])
  const g = groups[1]
  assert.deepEqual(g.picks.map((m) => m.id), ['m1'])
  assert.deepEqual(g.rejects.map((m) => m.id), ['m2'])
  assert.deepEqual(g.hearts.map((m) => m.id), ['m1', 'm3'])
  // accepted ones and rejects aren't offered again as picks
  assert.deepEqual(g.openPicks.map((m) => m.id), ['m1'])
  assert.deepEqual(g.comments.map((c) => c.id), ['c1', 'c2'])
  assert.equal(g.unseen, true)
  assert.equal(groups[2].has_newer_version, true)
})

test('texts', () => {
  assert.equal(countsText({ picks: 2, hearts: 1, rejects: 1, comments: 3 }), '2 picks · 1 ♥ · 1 reject · 3 comments')
  assert.equal(countsText({ picks: 1 }), '1 pick')
  assert.equal(countsText({}), '')
  assert.equal(tileBadgeText({ picks: 2, hearts: 3, rejects: 0, comments: 1 }), '✓2 ♥3')
  assert.equal(tileBadgeText({ picks: 0, hearts: 0, rejects: 1, comments: 2 }), '💬2')
  assert.equal(tileBadgeText({ picks: 0, hearts: 0, rejects: 1, comments: 0 }), '')
  assert.equal(tileBadgeText(undefined), '')
  assert.equal(namesText([{ guest_name: 'Ann' }]), 'Ann')
  assert.equal(namesText([{ guest_name: 'Ann' }, { guest_name: 'Bo' }, { guest_name: 'Ann' }]), 'Ann and Bo')
  assert.equal(namesText([{ guest_name: 'A' }, { guest_name: 'B' }, { guest_name: 'C' }, { guest_name: 'D' }]), 'A, B and 2 others')
  assert.equal(roundText({ enabled: true, round: 3, round_open: false }), 'Round 3 · closed')
  assert.equal(roundText({ enabled: false, round: 3, round_open: true }), 'Guest feedback off')
  assert.equal(acceptedText({ batch_id: 'b', accepted: ['a', 'b'], skipped: [] }), 'Accepted 2 pieces of guest feedback.')
  assert.match(acceptedText({ batch_id: null, accepted: [], skipped: [{ id: 'x', reason: 'gone' }] }), /Nothing accepted\. 1 skipped \(gone\)/)
  assert.equal(versionNote({ has_newer_version: false, versions: [1] }), '')
  assert.equal(versionNote({ has_newer_version: true, versions: [1] }), "Guests saw version 1; there's a newer version now")
  assert.equal(versionNote({ has_newer_version: false, versions: [1, 2] }), 'Guests saw versions 1, 2')
})

test('feedbackParams', () => {
  assert.equal(feedbackParams({}), 'limit=500')
  assert.equal(feedbackParams({ share_id: 'S', round: 2, item_id: 'I', unseen: true, limit: 10 }), 'share_id=S&round=2&item_id=I&unseen=true&limit=10')
})

test('/feedback is a screen in the library frame and keeps the share', () => {
  const p = fromUrl('/feedback', '?share=01ABC')
  assert.deepEqual(p.screen, { kind: 'feedback', share: '01ABC' })
  assert.equal(inLibraryFrame(p.screen), true)
  assert.equal(toUrl(p), '/feedback?share=01ABC')
  assert.equal(toUrl({ query: { ...emptyQuery }, smartId: null, item: null, full: false, screen: { kind: 'feedback' } }), '/feedback')
  assert.deepEqual(fromUrl('/feedback', '').screen, { kind: 'feedback', share: null })
})

test('the API helpers send what the server expects', async () => {
  const calls: { url: string; init: RequestInit }[] = []
  const realFetch = globalThis.fetch
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  try {
    await proofApi.patch('S 1', { enabled: true })
    await proofApi.accept({ mark_ids: ['m'], as: 'label:red' })
    await proofApi.seen()
    await proofApi.deleteComment('c1')
    await proofApi.newRound('S1')
  } finally {
    globalThis.fetch = realFetch
  }
  assert.equal(calls[0].url, '/api/shares/S%201/proofing')
  assert.equal(calls[0].init.method, 'PATCH')
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { enabled: true })
  assert.equal(calls[1].url, '/api/feedback/accept')
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), { mark_ids: ['m'], as: 'label:red' })
  assert.deepEqual(JSON.parse(String(calls[2].init.body)), { share_id: null })
  assert.equal(calls[3].init.method, 'DELETE')
  assert.equal(calls[4].url, '/api/shares/S1/proofing/rounds')
  assert.equal(calls[4].init.method, 'POST')
})

test('proofing.css keeps to its pf- prefix and theme tokens', () => {
  const css = readFileSync(new URL('../src/proofing.css', import.meta.url), 'utf8')
  for (const m of css.matchAll(/^([^\s@/}][^{]*)\{/gm)) {
    const sel = m[1]
    for (const cls of sel.matchAll(/\.([a-zA-Z][\w-]*)/g)) {
      assert.ok(cls[1].startsWith('pf-') || ['tile', 'badge', 'selected', 'rating', 'mobile', 'check-row'].includes(cls[1]), `${cls[1]} in ${sel}`)
    }
  }
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,6}\b/, 'colours come from theme tokens')
})
