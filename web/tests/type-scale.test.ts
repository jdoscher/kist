// Every font size is a step of the six-step scale (--fs-1 … --fs-6:
// 11/13/15/18/24/34 px). A raw size fails here, the same way a colour that
// misses AA fails contrast.test.ts. A genuinely needed exception carries a
// `/* type-scale: allow (why) */` comment on the same line.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { test } from 'node:test'

const dir = new URL('../src/', import.meta.url)
const sheets = readdirSync(dir)
  .filter((f: string) => f.endsWith('.css'))
  .map((f: string) => [f, readFileSync(new URL(f, dir), 'utf8')] as const)

const OK = /^(var\(--fs-[1-6]\)|inherit)$/

test('every font-size in the stylesheets uses the type scale', () => {
  const off: string[] = []
  for (const [file, css] of sheets) {
    css.split('\n').forEach((line: string, i: number) => {
      const m = line.match(/(?:^|[\s;{])font-size:\s*([^;]+);/)
      if (!m) return
      if (OK.test(m[1].trim())) return
      if (/type-scale: allow \(.+\)/.test(line)) return
      off.push(`${file}:${i + 1} font-size: ${m[1].trim()}`)
    })
  }
  assert.deepEqual(off, [])
})

test('no font shorthand sneaks a raw size past the scale', () => {
  const off: string[] = []
  for (const [file, css] of sheets) {
    css.split('\n').forEach((line: string, i: number) => {
      const m = line.match(/(?:^|[\s;{])font:\s*([^;]+);/)
      if (!m || m[1].trim() === 'inherit') return
      if (/\d(px|rem|em|pt|%)/.test(m[1]) && !/var\(--fs-[1-6]\)/.test(m[1])) off.push(`${file}:${i + 1} font: ${m[1]}`)
    })
  }
  assert.deepEqual(off, [])
})

test('the scale itself is the six documented steps', () => {
  const css = readFileSync(new URL('styles.css', dir), 'utf8')
  const steps = [...css.matchAll(/--fs-(\d):\s*(\d+)px;/g)].map((m) => [Number(m[1]), Number(m[2])])
  assert.deepEqual(steps, [
    [1, 11],
    [2, 13],
    [3, 15],
    [4, 18],
    [5, 24],
    [6, 34],
  ])
})

test('headings are reset: no browser margins or em sizes', () => {
  const css = readFileSync(new URL('styles.css', dir), 'utf8')
  const m = css.match(/^h1,\nh2,\nh3,\nh4,\nh5,\nh6 \{([^}]*)\}/m)
  assert.ok(m, 'no h1–h6 reset in styles.css')
  assert.match(m[1], /margin: 0;/)
  assert.match(m[1], /font-size: var\(--fs-\d\);/)
  assert.match(m[1], /font-weight: \d+;/)
})
