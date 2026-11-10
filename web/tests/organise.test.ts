// Namespaced tags, implied-by, and stacks in the grid. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Item } from '../src/api.ts'
import { canStack, expandedMemberIds, expandStacks, stackBadge, stacksOf } from '../src/stacks.ts'
import { groupByNamespace, impliedBy, isWildcard, namespaceWildcard, splitTag } from '../src/tagNs.ts'

test('namespaces parse like the server: brand:rolex, not a URL or a sentence', () => {
  assert.deepEqual(splitTag('brand:Rolex'), { namespace: 'brand', value: 'Rolex' })
  assert.deepEqual(splitTag('Brand:Rolex'), { namespace: 'brand', value: 'Rolex' })
  assert.deepEqual(splitTag('style:mid-century modern'), { namespace: 'style', value: 'mid-century modern' })
  for (const plain of ['Rolex', 'http://x.com', 'a b:c', ':x', 'x:', 'note: this', 'brand: rolex'])
    assert.deepEqual(splitTag(plain), { namespace: null, value: plain }, plain)
  assert.equal(namespaceWildcard('brand'), 'brand:*')
  assert.equal(isWildcard('brand:*'), true)
  assert.equal(isWildcard('brand:rolex'), false)
  assert.equal(isWildcard('a b:*'), false)
})

test('tags grouped by namespace, plain tags first', () => {
  const g = groupByNamespace([{ name: 'z' }, { name: 'style:minimal' }, { name: 'brand:Rolex' }, { name: 'Brand:Omega' }, { name: 'a' }])
  assert.deepEqual(
    g.map(([ns, tags]) => [ns, tags.map((t) => t.name)]),
    [
      [null, ['z', 'a']],
      ['brand', ['brand:Rolex', 'Brand:Omega']],
      ['style', ['style:minimal']],
    ],
  )
})

test('which of an item’s tags implies each implied tag, through chains', () => {
  const rules = [
    { tag: 'Boots', implied: 'Shoes' },
    { tag: 'Shoes', implied: 'Footwear' },
    { tag: 'Rolex', implied: 'Watches' },
  ]
  const by = impliedBy(['boots', 'rolex'], ['Shoes', 'Footwear', 'Watches'], rules)
  assert.equal(by.get('Shoes'), 'boots')
  assert.equal(by.get('Footwear'), 'boots')
  assert.equal(by.get('Watches'), 'rolex')
  // loops (refused by the server anyway) don't hang
  assert.equal(impliedBy(['a'], ['b'], [{ tag: 'a', implied: 'b' }, { tag: 'b', implied: 'a' }]).get('b'), 'a')
})

const it = (id: string, stack_id: string | null = null, stack_count: number | null = null) =>
  ({ id, stack_id, stack_count }) as Item

test('an open stack shows its members right after its entry, once', () => {
  const list = [it('a'), it('c1', 'S', 3), it('b')]
  const open = new Map([['S', [it('c0', 'S'), it('c1', 'S'), it('c2', 'S')]]])
  assert.deepEqual(expandStacks(list, open).map((i) => i.id), ['a', 'c1', 'c0', 'c2', 'b'])
  assert.deepEqual([...expandedMemberIds(list, open)], ['c0', 'c2'])
  // nothing open: the same list
  assert.equal(expandStacks(list, new Map()), list)
  // a member already listed elsewhere isn't repeated
  assert.deepEqual(expandStacks([it('c1', 'S', 3), it('c2', 'S')], open).map((i) => i.id), ['c1', 'c0', 'c2'])
})

test('stack and unstack in the bulk bar', () => {
  assert.equal(canStack([it('a')]), false)
  assert.equal(canStack([it('a'), it('b')]), true)
  assert.equal(canStack([it('a', 'S'), it('b', 'S')]), false)
  assert.equal(canStack([it('a', 'S'), it('b')]), true)
  assert.deepEqual(stacksOf([it('a', 'S'), it('b', 'T'), it('c', 'S'), it('d')]), ['S', 'T'])
  assert.deepEqual(stackBadge(3, false), { text: '3', label: 'Open stack of 3 items' })
  assert.equal(stackBadge(1, false), null)
  assert.equal(stackBadge(null, true), null)
})
