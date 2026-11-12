// Motion tokens: CSS and JS agree, and reduced motion turns them off.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { DUR_IN_MS, DUR_OUT_MS, exitDelay, prefersReducedMotion } from '../src/motion.ts'

const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8')

test('--dur-in / --dur-out match motion.ts, and the sheet curve is a token', () => {
  assert.match(css, new RegExp(`--dur-in: ${DUR_IN_MS}ms;`))
  assert.match(css, new RegExp(`--dur-out: ${DUR_OUT_MS}ms;`))
  assert.match(css, /--ease-sheet: cubic-bezier\(0\.32, 0\.72, 0, 1\);/)
  assert.match(css, /--dur-sheet: 380ms;/)
})

test('reduced motion zeroes every motion token and the press scale', () => {
  const i = css.indexOf('@media (prefers-reduced-motion: reduce)')
  assert.ok(i >= 0)
  const block = css.slice(i, i + 1200)
  for (const t of ['--dur-1', '--dur-2', '--dur-3', '--dur-in', '--dur-out', '--dur-sheet']) assert.match(block, new RegExp(`${t}: 0\\.01ms;`))
  assert.match(block, /animation-duration: 0\.01ms !important;/)
  assert.match(block, /button:active:not\(:disabled\) \{\s*transform: none;/)
})

test('a closing panel stays for the exit, unless motion is reduced', () => {
  assert.equal(exitDelay(false), DUR_OUT_MS)
  assert.equal(exitDelay(true), 0)
  assert.equal(prefersReducedMotion({ matchMedia: () => ({ matches: true }) }), true)
  assert.equal(prefersReducedMotion(undefined), false)
})
