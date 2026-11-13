// An overlay that swaps the page's text colours for the preview's (light
// text on a dark or grey surround) must swap every surface that text sits on,
// or a menu opened inside it shows light text on the light theme's white
// (the View menu in the full-screen preview, Nov 2026).
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { test } from 'node:test'

const SURFACES = ['--bg', '--bg-raised', '--bg-hover', '--bg-active', '--bg-float', '--float-hover', '--accent-soft', '--border']

test('overlays that use the preview text also use the preview surfaces', () => {
  const dir = new URL('../src/', import.meta.url)
  const missing: string[] = []
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.css'))) {
    const css = readFileSync(new URL(f, dir), 'utf8')
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*--text:\s*var\(--preview-text\)[^{}]*)\}/g)) {
      const sel = m[1].trim().split('\n').pop()
      for (const s of SURFACES) if (!new RegExp(`${s}:\\s*var\\(--preview-`).test(m[2])) missing.push(`${f} ${sel}: ${s}`)
    }
  }
  assert.deepEqual(missing, [])
})
