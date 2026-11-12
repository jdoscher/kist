// One control system (docs/brand.md "Controls"): one height, three kinds.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { test } from 'node:test'

const src = new URL('../src/', import.meta.url)
const styles = readFileSync(new URL('styles.css', src), 'utf8')

function files(dir: URL, ext: string): string[] {
  const out: string[] = []
  for (const f of readdirSync(dir)) {
    const u = new URL(f, dir)
    if (statSync(u).isDirectory()) out.push(...files(new URL(`${f}/`, dir), ext))
    else if (f.endsWith(ext)) out.push(readFileSync(u, 'utf8'))
  }
  return out
}

test('controls share one height and corner', () => {
  assert.match(styles, /--ctl-h: 28px;/)
  assert.match(styles, /--ctl-radius: 6px;/)
  const button = styles.match(/^button \{([^}]*)\}/m)
  assert.ok(button)
  assert.match(button[1], /min-height: var\(--ctl-h\);/)
  assert.match(button[1], /background: transparent;/) // quiet by default
  assert.match(button[1], /border: 1px solid transparent;/)
})

test('the primary kind is .btn-primary everywhere (no stray "primary" class)', () => {
  assert.match(styles, /^\.btn-primary \{[^}]*background: var\(--accent\);/m)
  for (const css of files(src, '.css')) assert.ok(!/button\.primary\b|\.primary\s*\{/.test(css))
  for (const tsx of files(src, '.tsx')) assert.ok(!/className=["'`{][^"'`]*\bprimary\b(?!-)/.test(tsx.replace(/btn-primary/g, '')))
})

test('selects are fields with their own chevron in every theme', () => {
  const sel = [...styles.matchAll(/^select \{([^}]*)\}/gm)].find((m) => m[1].includes('appearance'))
  assert.ok(sel)
  assert.match(sel[1], /appearance: none;/)
  assert.match(sel[1], /background-image: var\(--chevron\);/)
  const themes = readFileSync(new URL('themes.css', src), 'utf8')
  assert.equal(themes.match(/--chevron: url\(/g)?.length, 3)
})
