// Persisted prefs. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { clampSize, readPrefs, savePref } from '../src/settings.ts'

function memStore() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    m,
  }
}

test('defaults when nothing is stored or storage is unavailable', () => {
  const want = { panelOpen: true, gridLayout: 'justified', tileSize: 200, mobileCols: 3 }
  assert.deepEqual(readPrefs(memStore()), want)
  assert.deepEqual(readPrefs(null), want)
  const throwing = {
    getItem: () => {
      throw new Error('SecurityError')
    },
    setItem: () => {
      throw new Error('QuotaExceeded')
    },
  }
  assert.deepEqual(readPrefs(throwing), want)
  savePref('tileSize', 300, throwing) // must not throw
})

test('prefs round-trip and bad values are clamped', () => {
  const s = memStore()
  savePref('panelOpen', false, s)
  savePref('gridLayout', 'square', s)
  savePref('tileSize', 260, s)
  savePref('mobileCols', 2, s)
  assert.deepEqual(readPrefs(s), { panelOpen: false, gridLayout: 'square', tileSize: 260, mobileCols: 2 })
  assert.equal(s.m.get('kist.panelOpen'), '0') // same keys as before the split
  s.m.set('kist.tileSize', 'banana')
  assert.equal(readPrefs(s).tileSize, 200)
  assert.equal(clampSize(9999), 400)
  assert.equal(clampSize(10), 120)
})
