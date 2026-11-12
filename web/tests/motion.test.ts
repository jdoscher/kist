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

test('the phone sheet, drawer and bottom sheets move on the sheet curve (B12)', () => {
  const phone = readFileSync(new URL('../src/phone.css', import.meta.url), 'utf8')
  const rule = (sel: string) => {
    const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    return [...phone.matchAll(new RegExp(`^${esc} \\{([^}]*)\\}`, 'gm'))].map((m) => m[1]).join('\n')
  }
  assert.match(rule('.mobile .detail-sheet'), /animation: sheet-up var\(--dur-sheet\) var\(--ease-sheet\)/)
  assert.match(rule('.mobile .detail-sheet'), /transition: transform var\(--dur-sheet\) var\(--ease-sheet\)/)
  assert.match(rule('.mobile .drawer'), /transition: transform var\(--dur-sheet\) var\(--ease-sheet\)/)
  assert.match(rule('.mobile .drawer-backdrop'), /transition: opacity var\(--dur-sheet\) var\(--ease-sheet\)/)
  assert.match(rule('.mobile .modal.phone-sheet'), /animation: sheet-up var\(--dur-sheet\) var\(--ease-sheet\)/)
  // desktop motion stays on the short curve: the sheet tokens are phone-only
  for (const f of ['styles.css', 'm2.css', 'creative.css', 'm45.css', 'm6grid.css']) {
    const other = readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8')
    for (const m of other.matchAll(/^([^{}\n]*)\{[^}]*var\(--ease-sheet\)/gm)) assert.match(m[1], /\.mobile|:root/, `${f}: ${m[1].trim()}`)
  }
})

test('a closing panel stays for the exit, unless motion is reduced', () => {
  assert.equal(exitDelay(false), DUR_OUT_MS)
  assert.equal(exitDelay(true), 0)
  assert.equal(prefersReducedMotion({ matchMedia: () => ({ matches: true }) }), true)
  assert.equal(prefersReducedMotion(undefined), false)
})
