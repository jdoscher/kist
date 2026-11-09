// Appearance: theme resolution, density spacing, stored prefs. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readPrefs, savePref } from '../src/settings.ts'
import {
  DENSITY_GRID,
  densityGap,
  densityTileSize,
  isDensity,
  isThemePref,
  resolveTheme,
  THEME_COLOR,
} from '../src/theme.ts'
import { layoutRows, tilePositions } from '../src/layout.ts'

function memStore() {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m }
}

test('System follows the OS; an explicit theme wins', () => {
  assert.equal(resolveTheme('system', true), 'dark')
  assert.equal(resolveTheme('system', false), 'light')
  for (const dark of [true, false]) {
    assert.equal(resolveTheme('light', dark), 'light')
    assert.equal(resolveTheme('dark', dark), 'dark')
    assert.equal(resolveTheme('gallery', dark), 'gallery')
  }
})

test('theme and density default to System and Comfortable, and reject junk', () => {
  const s = memStore()
  assert.equal(readPrefs(s).theme, 'system')
  assert.equal(readPrefs(s).density, 'comfortable')
  s.m.set('kist.theme', 'neon')
  s.m.set('kist.density', 'huge')
  assert.equal(readPrefs(s).theme, 'system')
  assert.equal(readPrefs(s).density, 'comfortable')
  savePref('theme', 'gallery', s)
  savePref('density', 'compact', s)
  assert.equal(readPrefs(s).theme, 'gallery')
  assert.equal(readPrefs(s).density, 'compact')
  assert.ok(isThemePref('dark') && !isThemePref('Dark') && !isThemePref(null))
  assert.ok(isDensity('gallery') && !isDensity(''))
})

test('every theme has a browser chrome colour', () => {
  for (const t of ['light', 'dark', 'gallery'] as const) assert.match(THEME_COLOR[t], /^#[0-9A-F]{6}$/i)
})

test('density changes the gaps and (gallery) the tile size', () => {
  assert.equal(densityGap('comfortable'), 8) // the classic 4 px margins
  assert.ok(densityGap('compact') < densityGap('comfortable'))
  assert.ok(densityGap('gallery') > densityGap('comfortable'))
  assert.equal(densityTileSize(200, 'comfortable'), 200)
  assert.ok(densityTileSize(200, 'gallery') > 200)
  // rows still fill the width exactly with each density's gap
  const items = Array.from({ length: 40 }, (_, i) => ({ width: 300 + (i % 5) * 120, height: 400 }))
  for (const d of Object.keys(DENSITY_GRID) as (keyof typeof DENSITY_GRID)[]) {
    const gap = densityGap(d)
    const rows = layoutRows(items, 1200, densityTileSize(200, d), 'justified', gap)
    for (const row of rows.slice(0, -1)) {
      const used = row.tiles.reduce((a, t) => a + t.width + gap, 0)
      assert.equal(used, 1200, `${d} row width`)
    }
    const pos = tilePositions(rows, gap)
    assert.equal(pos[0].center, gap / 2 + rows[0].tiles[0].width / 2)
  }
})
