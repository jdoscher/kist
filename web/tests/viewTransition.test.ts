// Grid <-> preview transition rules. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { canTransition, heroFor } from '../src/viewTransition.ts'

test('only opening and closing animate, not moving inside the preview', () => {
  assert.equal(heroFor(null, 'a'), 'a')
  assert.equal(heroFor('a', null), 'a')
  assert.equal(heroFor('a', 'b'), null)
  assert.equal(heroFor(null, null), null)
})

test('no API or reduced motion: no transition', () => {
  const doc = { startViewTransition: () => ({ finished: Promise.resolve() }) }
  assert.equal(canTransition(doc, false), true)
  assert.equal(canTransition(doc, true), false)
  assert.equal(canTransition({}, false), false)
  assert.equal(canTransition(undefined, false), false)
})
