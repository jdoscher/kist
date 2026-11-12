// Icons: three sizes, one stroke, one meaning per glyph (docs/brand.md).
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { test } from 'node:test'

const src = new URL('../src/', import.meta.url)

function tsx(dir: URL): [string, string][] {
  const out: [string, string][] = []
  for (const f of readdirSync(dir)) {
    const u = new URL(f, dir)
    if (statSync(u).isDirectory()) out.push(...tsx(new URL(`${f}/`, dir)))
    else if (f.endsWith('.tsx')) out.push([u.pathname.split('/src/')[1], readFileSync(u, 'utf8')])
  }
  return out
}
const files = tsx(src)

// glyphs drawn as illustrations, not icons: the preview's big arrows, the
// empty-state and missing-image glyphs (each named here on purpose)
const ILLUSTRATION = new Set(['components/Preview.tsx:36', 'components/m3/BoardsScreen.tsx:28', 'components/m45/AddPage.tsx:28', 'components/Organise.tsx:32'])

test('icon sizes are 14, 16 or 20', () => {
  const off: string[] = []
  for (const [name, s] of files)
    for (const m of s.matchAll(/<[A-Z][\w.]*[^<>]*?\ssize=\{(\d+)\}/g)) {
      const n = Number(m[1])
      if (![14, 16, 20].includes(n) && !ILLUSTRATION.has(`${name}:${n}`)) off.push(`${name}: size ${n}`)
    }
  assert.deepEqual(off, [])
})

test('no per-icon stroke widths: the 1.5 px stroke comes from CSS', () => {
  const off: string[] = []
  for (const [name, s] of files)
    for (const m of s.matchAll(/<[A-Z][\w.]*[^<>]*?strokeWidth[=:]\s*\{?([\d.]+)/g)) if (m[1] !== '0') off.push(`${name}: strokeWidth ${m[1]}`)
  assert.deepEqual(off, [])
  const css = readFileSync(new URL('styles.css', src), 'utf8')
  assert.match(css, /\.lucide \{[^}]*stroke-width: 1\.5;/)
  assert.match(css, /\.lucide \* \{\s*vector-effect: non-scaling-stroke;/)
})

test('one meaning per glyph: Sparkles is AI only, Picks is a Flag', () => {
  const palette = files.find(([n]) => n.endsWith('CommandPalette.tsx'))![1]
  assert.match(palette, /picks: Flag,/)
  assert.match(palette, /theme: SunMoon,/)
  assert.match(palette, /boards: Presentation,/)
  assert.match(palette, /all: LayoutGrid,/)
  assert.ok(!/Sparkles,\n/.test(palette.slice(palette.indexOf('const ACTION_ICON'))))
  for (const [name, s] of files) {
    if (!/\bSparkles\b/.test(s)) continue
    // allowed: AI search modes, AI suggestions, More like this (embeddings)
    assert.ok(['components/SearchModes.tsx', 'components/GridExtras.tsx', 'components/ItemAI.tsx', 'components/m3/CommandPalette.tsx'].includes(name), `Sparkles in ${name}`)
  }
})
