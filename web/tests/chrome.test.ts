// Gallery grey is one surface: every bar or side panel that sits next to the
// images (screen heads, board bar, settings nav ...) uses the chrome tokens,
// never the floating --bg-raised step or the dark --border line.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const sheets = ['styles.css', 'm2.css', 'creative.css', 'm45.css', 'phone.css', 'm6grid.css'].map((f) =>
  readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8'),
)
const css = sheets.join('\n')

const CHROME = [
  '.topbar',
  '.sidebar',
  '.detail-panel',
  '.screen-head',
  '.board-bar',
  '.boards-head',
  '.triage-bar',
  '.triage-side',
  '.settings-head',
  '.settings-nav',
  '.board-library',
  '.map-list',
]

function rule(sel: string): string {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const m = css.match(new RegExp(`^${esc} \\{([^}]*)\\}`, 'm'))
  assert.ok(m, `no top-level rule for ${sel}`)
  return m[1]
}

test('chrome surfaces use --chrome / --chrome-line, not the second grey', () => {
  for (const sel of CHROME) {
    const body = rule(sel)
    assert.ok(!/var\(--bg-raised\)/.test(body), `${sel} uses --bg-raised`)
    assert.ok(!/var\(--border\)/.test(body), `${sel} uses --border`)
    assert.match(body, /background: var\(--chrome\)/, `${sel} has no --chrome background`)
  }
})
