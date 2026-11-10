// The board on a phone (click-test 2.0): view only, so no editing words,
// and the name keeps room instead of shrinking to one letter.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { boardListCaption } from '../src/board.ts'

test('the list caption only offers editing where the board can be edited', () => {
  assert.match(boardListCaption(9, true), /^9 things on the board, top first\. Select one to edit/)
  assert.equal(boardListCaption(9, false), '9 things on the board, top first.')
  assert.equal(boardListCaption(1, false), '1 thing on the board, top first.')
})

test('the phone board bar keeps a readable name width', () => {
  const css = readFileSync(new URL('../src/creative.css', import.meta.url), 'utf8')
  const rule = css.match(/\.board-screen\.mobile \.board-name \{([^}]*)\}/)
  assert.ok(rule, 'phone board-name rule exists')
  const min = rule[1].match(/min-width:\s*([\d.]+)(em|px)/)
  assert.ok(min, 'phone board-name sets a min-width')
  const px = Number(min[1]) * (min[2] === 'em' ? 16 : 1)
  assert.ok(px >= 96, `min-width ${min[0]} leaves room for a name`)
})
