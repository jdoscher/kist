// Culling keys, rename patterns, palette ranking, annotations, setup
// checklist and the Milestone 3 routes. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { activeAt, annotationWhere, boxFrom, fmtTime, rangeOnBar, sortAnnotations, toFraction, type Annotation } from '../src/annotate.ts'
import { advances, nextUndecided, swipeAction, triageKey } from '../src/cull.ts'
import { mcpCommand, progress, readSetup, saveSetup, setupSteps } from '../src/onboarding.ts'
import { matchScore, mergeResults, moveActive, pushRecent, rankLocal, readRecent, type PaletteEntry } from '../src/palette.ts'
import { patternErrors, renameAll, renameOne, strftime } from '../src/rename.ts'
import { emptyQuery } from '../src/api.ts'
import { stillMatches } from '../src/query.ts'
import { fromUrl, historyAction, toUrl } from '../src/url.ts'
import { afterVersionUpload } from '../src/versions.ts'

const key = (k: string, mods: Partial<{ metaKey: boolean; ctrlKey: boolean; altKey: boolean }> = {}) => ({
  key: k,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  ...mods,
})

test('triage keys: P/X/U flag, 0-5 rate, 6-9 label, T, F, arrows, undo', () => {
  assert.deepEqual(triageKey(key('p')), { kind: 'flag', flag: 'pick' })
  assert.deepEqual(triageKey(key('X')), { kind: 'flag', flag: 'reject' })
  assert.deepEqual(triageKey(key('u')), { kind: 'flag', flag: null })
  assert.deepEqual(triageKey(key('0')), { kind: 'rate', rating: 0 })
  assert.deepEqual(triageKey(key('5')), { kind: 'rate', rating: 5 })
  assert.deepEqual(triageKey(key('6')), { kind: 'label', label: 'red' })
  assert.deepEqual(triageKey(key('9')), { kind: 'label', label: 'green' })
  assert.deepEqual(triageKey(key('t')), { kind: 'tag' })
  assert.deepEqual(triageKey(key('f')), { kind: 'folder' })
  assert.deepEqual(triageKey(key('ArrowRight')), { kind: 'next' })
  assert.deepEqual(triageKey(key('ArrowLeft')), { kind: 'prev' })
  assert.deepEqual(triageKey(key('z', { metaKey: true })), { kind: 'undo' })
  assert.equal(triageKey(key('p', { metaKey: true })), null, '⌘P is the browser’s')
  assert.equal(triageKey(key('q')), null)
  assert.ok(advances({ kind: 'flag', flag: 'pick' }))
  assert.ok(!advances({ kind: 'tag' }))
})

test('phone swipes: up keeps, down rejects, sideways moves', () => {
  assert.deepEqual(swipeAction('up'), { kind: 'flag', flag: 'pick' })
  assert.deepEqual(swipeAction('down'), { kind: 'flag', flag: 'reject' })
  assert.deepEqual(swipeAction('left'), { kind: 'next' })
  assert.deepEqual(swipeAction('right'), { kind: 'prev' })
  assert.equal(swipeAction(null), null)
})

test('survey moves to the next undecided item, wrapping', () => {
  assert.equal(nextUndecided(['a', 'b', 'c'], { b: 'pick' }, 'a'), 'c')
  assert.equal(nextUndecided(['a', 'b', 'c'], { c: 'reject' }, 'b'), 'a')
  assert.equal(nextUndecided(['a', 'b'], { a: 'pick', b: 'reject' }, 'a'), null)
})

const src = {
  filename: 'IMG_0042.JPG',
  ext: 'jpg',
  width: 1920,
  height: 1080,
  created_at: '2026-11-09T23:30:05Z',
  tags: ['client:acme', 'brand:kist'],
  folder: 'Shoots',
}

test('rename patterns render like the server', () => {
  assert.equal(renameOne('{name}_{n:3}', src, 7), 'IMG_0042_007.jpg')
  assert.equal(renameOne('{date:%Y-%m-%d}_{w}x{h}.{ext}', src, 1), '2026-11-09_1920x1080.jpg')
  assert.equal(renameOne('{folder}/{tag:client}', src, 1), 'Shoots-acme.jpg')
  assert.equal(renameOne('{tag:none}x', { ...src, width: null }, 1), 'x.jpg')
  assert.equal(renameOne('{w}', { ...src, width: null }, 1), '0.jpg')
  assert.equal(strftime('%H:%M:%S %%', new Date('2026-01-02T03:04:05Z')), '03:04:05 %')
})

test('rename numbers clashes like the server', () => {
  const items = [src, src, { ...src, filename: 'b.png', ext: 'png' }]
  assert.deepEqual(renameAll('shoot', items), ['shoot.jpg', 'shoot (2).jpg', 'shoot.png'])
  assert.deepEqual(renameAll('{n}', items, 10), ['10.jpg', '11.jpg', '12.png'])
})

test('rename pattern mistakes are explained', () => {
  assert.deepEqual(patternErrors('{name}_{n:3}'), [])
  assert.match(patternErrors('{name')[0], /Braces/)
  assert.match(patternErrors('{nmae}')[0], /Unknown placeholder \{nmae\}/)
  assert.match(patternErrors('')[0], /Type a pattern/)
  assert.match(patternErrors('a/b')[0], /“\/”/)
})

test('palette ranking: exact, prefix, word start, contains, fuzzy', () => {
  assert.equal(matchScore('board', 'Board'), 0)
  assert.equal(matchScore('new', 'New board'), 1)
  assert.equal(matchScore('board', 'New board'), 2)
  assert.equal(matchScore('oar', 'New board'), 3)
  assert.equal(matchScore('nbrd', 'New board'), 5)
  assert.equal(matchScore('zzz', 'New board'), null)
  assert.equal(matchScore('dark', 'Toggle theme', 'dark light'), 2)
  const entries: PaletteEntry[] = [
    { kind: 'action', id: 'a', title: 'Go to Trash' },
    { kind: 'action', id: 'b', title: 'Trash selected' },
    { kind: 'action', id: 'c', title: 'Settings' },
  ]
  assert.deepEqual(rankLocal('trash', entries).map((e) => e.id), ['b', 'a'])
  assert.deepEqual(rankLocal('', entries).map((e) => e.id), ['a', 'b', 'c'])
})

test('palette merges web actions with server results by kind, without repeats', () => {
  const local: PaletteEntry[] = [{ kind: 'action', id: 'new-board', title: 'New board' }]
  const server: PaletteEntry[] = [
    { kind: 'item', id: 'i1', title: 'board.png' },
    { kind: 'action', id: 'new-board', title: 'New board' },
    { kind: 'board', id: 'b1', title: 'Board one' },
    { kind: 'action', id: 'trash', title: 'Trash' },
  ]
  const groups = mergeResults('board', local, server)
  assert.deepEqual(groups.map((g) => g.kind), ['action', 'board', 'item'])
  assert.equal(groups[0].entries.length, 1)
  const recent = [{ kind: 'folder' as const, id: 'f', title: 'Shoots' }]
  assert.equal(mergeResults('', local, [], recent)[0].kind, 'recent')
  assert.equal(mergeResults('x', local, [], recent).some((g) => g.kind === 'recent'), false)
  assert.equal(moveActive(0, -1, 5), 4)
  assert.equal(moveActive(4, 1, 5), 0)
  assert.equal(moveActive(0, 1, 0), -1)
})

test('palette recents: newest first, no duplicates, capped', () => {
  const mem = new Map<string, string>()
  const s = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) }
  for (let i = 0; i < 8; i++) pushRecent({ kind: 'folder', id: `f${i}`, title: `F${i}` }, s)
  pushRecent({ kind: 'folder', id: 'f3', title: 'F3' }, s)
  const r = readRecent(s)
  assert.equal(r.length, 6)
  assert.equal(r[0].id, 'f3')
  assert.equal(r.filter((e) => e.id === 'f3').length, 1)
  assert.deepEqual(readRecent({ getItem: () => 'not json', setItem: () => {} }), [])
})

const ann = (o: Partial<Annotation>): Annotation => ({
  id: 'a',
  item_id: 'i',
  kind: 'point',
  x: null,
  y: null,
  w: null,
  h: null,
  t_start: null,
  t_end: null,
  text: '',
  author: 'web',
  created_at: '2026-11-09T00:00:00Z',
  resolved: false,
  ...o,
})

test('annotation geometry: fractions, boxes, times', () => {
  assert.deepEqual(toFraction({ left: 100, top: 50, width: 200, height: 100 }, 150, 100), { x: 0.25, y: 0.5 })
  assert.deepEqual(toFraction({ left: 0, top: 0, width: 100, height: 100 }, -20, 500), { x: 0, y: 1 })
  assert.deepEqual(boxFrom({ x: 0.5, y: 0.6 }, { x: 0.2, y: 0.1 }), { x: 0.2, y: 0.1, w: 0.3, h: 0.5 })
  assert.equal(boxFrom({ x: 0.5, y: 0.5 }, { x: 0.501, y: 0.6 }), null)
  assert.equal(fmtTime(65), '1:05')
  assert.equal(fmtTime(3723), '1:02:03')
  assert.equal(fmtTime(7.5, true), '0:07.5')
  assert.equal(fmtTime(null), '')
  assert.deepEqual(rangeOnBar(2, 4, 8), { left: '25.000%', width: '25.000%' })
  assert.deepEqual(rangeOnBar(2, null, 8).left, '25.000%')
  const list = [ann({ id: 'r', kind: 'range', t_start: 2, t_end: 4 }), ann({ id: 'm', kind: 'range', t_start: 6 }), ann({ id: 'done', resolved: true, x: 0, y: 0 })]
  assert.deepEqual(activeAt(list, 3).map((a) => a.id), ['r'])
  assert.deepEqual(activeAt(list, 6.3).map((a) => a.id), ['m'])
  assert.deepEqual(sortAnnotations(list).map((a) => a.id), ['r', 'm', 'done'])
  assert.equal(annotationWhere(list[0]), '0:02–0:04')
  assert.equal(annotationWhere(ann({ kind: 'box', x: 0.1, y: 0.25 })), 'box at 10% across, 25% down')
})

test('setup checklist ticks itself from real state', () => {
  const none = setupSteps({})
  assert.equal(progress(none).done, 0)
  const some = setupSteps({ totalItems: 12, actors: ['Firefox extension'], sources: ['web', 'mcp'], backupOk: true })
  assert.deepEqual(
    some.filter((s) => s.done).map((s) => s.id),
    ['items', 'extension', 'agent', 'backup'],
  )
  assert.ok(some.every((s) => !s.done || s.auto))
  const manual = setupSteps({ manual: { phone: true } })
  assert.ok(manual.find((s) => s.id === 'phone')?.done)
  assert.ok(!manual.find((s) => s.id === 'phone')?.auto)
  const mem = new Map<string, string>()
  const s = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) }
  saveSetup({ dismissed: true, manual: { phone: true }, standalone: false }, s)
  assert.deepEqual(readSetup(s), { dismissed: true, manual: { phone: true }, standalone: false })
  assert.match(mcpCommand('https://kist.example.com/'), /https:\/\/kist\.example\.com\/mcp/)
})

test('boards and triage have their own addresses', () => {
  const boards = fromUrl('/boards', '')
  assert.deepEqual(boards.screen, { kind: 'boards' })
  assert.equal(toUrl(boards), '/boards')
  const board = fromUrl('/board/01ABC', '')
  assert.deepEqual(board.screen, { kind: 'board', id: '01ABC' })
  assert.equal(toUrl(board), '/board/01ABC')
  const triage = fromUrl('/triage', '?view=unfiled')
  assert.deepEqual(triage.screen, { kind: 'triage', view: 'unfiled', folder: null })
  assert.equal(toUrl(triage), '/triage?view=unfiled')
  assert.equal(toUrl(fromUrl('/triage', '?view=all&folder=F1')), '/triage?view=all&folder=F1')
  const lib = fromUrl('/all', '')
  assert.equal(historyAction(lib, board, { mobile: false, curPushed: false }), 'push')
})

test('flag and label filters ride in the URL and narrow lists in place', () => {
  const p = fromUrl('/all', '?flag=pick&label=red')
  assert.equal(p.query.flag, 'pick')
  assert.equal(p.query.label, 'red')
  assert.equal(toUrl(p), '/all?flag=pick&label=red')
  assert.equal(fromUrl('/all', '?flag=bogus').query.flag, null)
  const item = { state: 'active', tags: [], folder_ids: [], kind: 'image', rating: 0, preview_status: 'ready', flag: 'pick', label: null }
  const q = { ...emptyQuery, flag: 'pick' }
  assert.ok(stillMatches(item as never, q))
  assert.ok(!stillMatches({ ...item, flag: null } as never, q))
  assert.ok(stillMatches({ ...item, label: null } as never, { ...emptyQuery, label: 'none' }))
})

test('after a new version: select it, reload the stack, offer Compare', () => {
  const next = afterVersionUpload({ item: { id: 'v2', filename: 'logo-v2.png' }, created: true }, 'v1')
  assert.equal(next.select, 'v2')
  assert.deepEqual(next.compare, ['v1', 'v2'])
  assert.ok(next.invalidate.includes('stack') && next.invalidate.includes('item'))
  assert.match(next.message, /newest version/)
  // the same file again: shown, nothing to compare
  const dup = afterVersionUpload({ item: { id: 'v1', filename: 'logo.png' }, created: false }, 'v1')
  assert.equal(dup.compare, null)
  assert.match(dup.message, /already in the library/)
})
