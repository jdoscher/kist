// The keyboard map: one binding per key on each screen, the handlers agree
// with the table, and the README shows the table. Run with `npm test`.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { triageKey } from '../src/cull.ts'
import { duplicateBindings, KEYMAP, keymapMarkdown, modKeyLabel, normalizeKey, type KeyContext } from '../src/keymap.ts'

const src = (p: string) => readFileSync(new URL(`../src/${p}`, import.meta.url), 'utf8')

test('normalizeKey spells a chord one way', () => {
  assert.equal(normalizeKey('Mod+Shift+g'), 'Shift+Mod+G')
  assert.equal(normalizeKey('Shift+Mod+G'), 'Shift+Mod+G')
  assert.equal(normalizeKey('+'), '+')
  assert.equal(normalizeKey('Shift++'), 'Shift++')
  assert.equal(normalizeKey('s'), 'S')
  assert.equal(normalizeKey('ArrowLeft'), 'ArrowLeft')
})

test('no key does two things on one screen', () => {
  assert.deepEqual(duplicateBindings(), [])
  // and the check itself catches one
  assert.equal(duplicateBindings({ x: [{ keys: ['s'], does: 'a' }, { keys: ['S'], does: 'b' }] }).length, 1)
})

test('the M2 stack key and the M3 survey key no longer share S', () => {
  const lib = KEYMAP.library
  const does = (k: string) => lib.filter((b) => b.keys.map(normalizeKey).includes(k)).map((b) => b.does)
  assert.deepEqual(does('S').length, 1)
  assert.match(does('S')[0], /stack/)
  assert.match(does('N')[0], /survey/)
  // the handler agrees
  assert.match(src('components/m3/Creative.tsx'), /k !== 'c' && k !== 'n'/)
})

test('every triage key in the table is a triage action, and the reverse', () => {
  const ev = (key: string) => {
    const mod = key.startsWith('Mod+')
    return { key: mod ? key.slice(4).toLowerCase() : key.length === 1 ? key.toLowerCase() : key, metaKey: mod, ctrlKey: false, altKey: false }
  }
  const listed = KEYMAP.triage.flatMap((b) => b.keys).filter((k) => k !== 'Escape' && k !== 'Mod+K')
  for (const k of listed) assert.ok(triageKey(ev(k)), `triage table lists ${k} but triageKey ignores it`)
  const set = new Set(listed.map(normalizeKey))
  for (const k of 'abcdefghijklmnopqrstuvwxyz0123456789')
    if (triageKey(ev(k))) assert.ok(set.has(normalizeKey(k)), `triageKey handles ${k}, the table doesn't list it`)
})

// letter/digit keys each handler compares against, by file
const HANDLERS: [string, KeyContext[]][] = [
  ['components/Grid.tsx', ['library']],
  ['components/m3/Creative.tsx', ['library', 'preview']],
  ['App.tsx', ['library']],
  ['components/Preview.tsx', ['preview']],
  ['components/m3/Annotations.tsx', ['preview']],
  ['components/m3/QuickLook.tsx', ['quicklook']],
  ['components/m3/board/keys.ts', ['board']],
  ['components/m3/Compare.tsx', ['compare']],
  ['viewAids.ts', ['preview', 'compare']],
]

test('every letter or digit a handler checks is in the table for its screen', () => {
  for (const [file, ctxs] of HANDLERS) {
    const text = src(file)
    const keys = new Set<string>()
    for (const m of text.matchAll(/\b(?:k|key|e\.key|e\.key\.toLowerCase\(\))\s*[!=]==\s*'([a-zA-Z0-9+=-])'/g)) keys.add(normalizeKey(m[1]))
    const listed = new Set(ctxs.flatMap((c) => KEYMAP[c].flatMap((b) => b.keys.map(normalizeKey))).map((k) => k.replace(/^.*\+(?=.)/, '')))
    for (const k of keys) assert.ok(listed.has(k), `${file} handles “${k}” but keymap.ts doesn't list it for ${ctxs.join('/')}`)
  }
})

test('the README keyboard section is the table', () => {
  const readme = readFileSync(new URL('../../README.md', import.meta.url), 'utf8')
  const start = readme.indexOf('<!-- keymap:start -->')
  const end = readme.indexOf('<!-- keymap:end -->')
  assert.ok(start >= 0 && end > start, 'README needs <!-- keymap:start --> … <!-- keymap:end -->')
  const body = readme.slice(start + '<!-- keymap:start -->'.length, end).trim() + '\n'
  assert.equal(body, keymapMarkdown(), 'regenerate the README table from keymapMarkdown()')
})

test('the search box keycap shows ⌘ on Apple devices and Ctrl elsewhere', () => {
  assert.equal(modKeyLabel('MacIntel'), '⌘')
  assert.equal(modKeyLabel('iPhone'), '⌘')
  assert.equal(modKeyLabel('Win32'), 'Ctrl ')
  assert.equal(modKeyLabel('Linux x86_64'), 'Ctrl ')
})
