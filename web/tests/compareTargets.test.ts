import assert from 'node:assert/strict'
import { test } from 'node:test'
import { COMPARE_MAX, sheetCompareChoices, stackChoice, versionChoice } from '../src/compareTargets.ts'
import { compareActions, comparePickEntries } from '../src/palette.ts'

const chain = [{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }]

test('Compare with vN: the version before the open one', () => {
  assert.deepEqual(versionChoice(chain, 'v3'), { key: 'version', label: 'Compare with v2', ids: ['v2', 'v3'] })
  assert.deepEqual(versionChoice(chain, 'v2'), { key: 'version', label: 'Compare with v1', ids: ['v1', 'v2'] })
  assert.equal(versionChoice(chain, 'v1'), null) // nothing before the first
  assert.equal(versionChoice([], 'x'), null)
  assert.equal(versionChoice([{ id: 'x' }], 'x'), null)
})

test('Compare with stack: the open item first, then the cover, at most four', () => {
  const stack = { cover_item_id: 'c', items: [{ id: 'a' }, { id: 'c' }, { id: 'b' }] }
  assert.deepEqual(stackChoice(stack, 'b'), { key: 'stack', label: 'Compare with stack', ids: ['b', 'c', 'a'] })
  assert.deepEqual(stackChoice(stack, 'c')?.ids, ['c', 'a', 'b'])
  const big = { cover_item_id: 's0', items: Array.from({ length: 7 }, (_, i) => ({ id: `s${i}` })) }
  const r = stackChoice(big, 's5')
  assert.equal(r?.ids.length, COMPARE_MAX)
  assert.deepEqual(r?.ids, ['s5', 's0', 's1', 's2'])
  assert.equal(r?.label, 'Compare with stack (4 of 7)')
  assert.equal(stackChoice({ cover_item_id: 'a', items: [{ id: 'a' }] }, 'a'), null) // alone
  assert.equal(stackChoice(null, 'a'), null)
  // a cover that isn't a member (stale) isn't compared
  assert.deepEqual(stackChoice({ cover_item_id: 'gone', items: [{ id: 'a' }, { id: 'b' }] }, 'a')?.ids, ['a', 'b'])
})

test('the phone sheet offers each pair once', () => {
  // two versions are a two-item stack: one button, "Compare with v1"
  const pair = { cover_item_id: 'v2', items: [{ id: 'v1' }, { id: 'v2' }] }
  assert.deepEqual(
    sheetCompareChoices([{ id: 'v1' }, { id: 'v2' }], pair, 'v2').map((c) => c.label),
    ['Compare with v1'],
  )
  // a stack with more in it: both
  const more = { cover_item_id: 'v3', items: [{ id: 'v1' }, { id: 'v2' }, { id: 'v3' }] }
  assert.deepEqual(sheetCompareChoices(chain, more, 'v3').map((c) => c.label), ['Compare with v2', 'Compare with stack'])
  // a stacked item with no versions (a RAW+JPEG pair, a burst)
  assert.deepEqual(sheetCompareChoices([], pair, 'v1').map((c) => c.ids), [['v1', 'v2']])
  assert.deepEqual(sheetCompareChoices(undefined, undefined, 'x'), [])
})

test('⌘K Compare…: compares a selection, or picks a second item for one', () => {
  assert.deepEqual(compareActions(0), [])
  const one = compareActions(1)
  assert.deepEqual(one.map((a) => [a.id, a.title]), [['compare-with', 'Compare…']])
  const many = compareActions(6)
  assert.deepEqual(many.map((a) => a.id), ['compare', 'survey'])
  assert.equal(many[0].title, 'Compare…')
  assert.match(many[0].subtitle ?? '', /^4 selected/)
})

test('the Compare… pick: items in view with no query, server items with one, never the open item', () => {
  const inView = [{ id: 'open', filename: 'open.jpg' }, { id: 'b', filename: 'b.jpg' }, { id: 'c', filename: 'c.jpg' }]
  const server = [
    { kind: 'folder' as const, id: 'f', title: 'Shoes' },
    { kind: 'item' as const, id: 'open', title: 'open.jpg' },
    { kind: 'item' as const, id: 'z', title: 'zebra.jpg' },
  ]
  assert.deepEqual(comparePickEntries('', server, inView, 'open').map((e) => e.id), ['b', 'c'])
  assert.deepEqual(comparePickEntries('  ', server, inView, 'open').map((e) => e.kind), ['item', 'item'])
  assert.deepEqual(comparePickEntries('ze', server, inView, 'open').map((e) => e.id), ['z'])
  // typing: matching items in view first, then the library's, each once
  assert.deepEqual(comparePickEntries('c.jpg', [{ kind: 'item', id: 'c', title: 'c.jpg' }, { kind: 'item', id: 'c2', title: 'c.jpg.png' }], inView, 'open').map((e) => e.id), ['c', 'c2'])
  assert.equal(comparePickEntries('', [], Array.from({ length: 20 }, (_, i) => ({ id: `i${i}`, filename: `${i}` })), 'x').length, 8)
})
