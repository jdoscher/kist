// Feature stylesheets are global: a bare class defined in two of them styles
// both features. The colour palette (m2.css) once used `.palette`, which also
// matched the ⌘K palette dialog (`.modal.palette`) and collapsed it to 30 px.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { test } from 'node:test'

const dir = new URL('../src/', import.meta.url)

function bareClasses(css: string): Set<string> {
  // top-level rules whose whole selector is a single class: `.name {`
  const out = new Set<string>()
  for (const m of css.matchAll(/^\.([a-zA-Z][\w-]*)\s*\{/gm)) out.add(m[1])
  return out
}

function compoundClasses(css: string): Set<string> {
  // classes used as `.a.b` (e.g. `.modal.palette`) anywhere
  const out = new Set<string>()
  for (const m of css.matchAll(/\.[a-zA-Z][\w-]*\.([a-zA-Z][\w-]*)/g)) out.add(m[1])
  return out
}

// shared utility classes that are meant to combine with anything
const UTILITIES = new Set(['grow', 'error'])

test('no bare class in one stylesheet is a compound modifier in another', () => {
  const files = readdirSync(dir).filter((f: string) => f.endsWith('.css'))
  const sheets = files.map((f: string) => [f, readFileSync(new URL(f, dir), 'utf8')] as const)
  const clashes: string[] = []
  for (const [a, cssA] of sheets) {
    for (const [b, cssB] of sheets) {
      if (a === b) continue
      for (const cls of bareClasses(cssA)) {
        if (UTILITIES.has(cls)) continue
        if (compoundClasses(cssB).has(cls)) clashes.push(`.${cls} (bare in ${a}, modifier in ${b})`)
      }
    }
  }
  assert.deepEqual(clashes, [])
})
