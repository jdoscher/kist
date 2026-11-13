// F5 viewing aids: keys, filters, grid, tiles, true 100 %, the store.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  actualSize,
  actualSource,
  aidsSummary,
  anyAid,
  clampGridN,
  containedRect,
  createAidsStore,
  DEFAULT_AIDS,
  escapeAids,
  filterKind,
  gridLines,
  posterizeTable,
  tileCells,
  trueZoom,
  viewAidKey,
  type ViewAids,
} from '../src/viewAids.ts'

const k = (key: string, mods: Partial<{ metaKey: boolean; ctrlKey: boolean; altKey: boolean }> = {}) => ({
  key,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  ...mods,
})

test('keys toggle and cycle the aids', () => {
  let a: ViewAids = DEFAULT_AIDS
  a = viewAidKey(k('g'), a)!
  assert.equal(a.grey, true)
  a = viewAidKey(k('V'), a)!
  assert.equal(a.posterize, 3)
  a = viewAidKey(k('v'), a)!
  assert.equal(a.posterize, 5)
  a = viewAidKey(k('v'), a)!
  assert.equal(a.posterize, 0)
  // turning greyscale off also drops posterise
  a = viewAidKey(k('v'), a)!
  a = viewAidKey(k('g'), a)!
  assert.equal(a.grey, false)
  assert.equal(a.posterize, 0)
  assert.equal(viewAidKey(k('h'), a)!.mirror, true)
  const grids = [] as string[]
  for (let i = 0; i < 4; i++) grids.push((a = viewAidKey(k('l'), a)!).grid)
  assert.deepEqual(grids, ['thirds', '4x4', 'custom', 'off'])
  a = viewAidKey(k('t'), a)!
  assert.equal(a.tile, 2)
  // 100 % and tile replace each other
  a = viewAidKey(k('1'), a)!
  assert.equal(a.actual, true)
  assert.equal(a.tile, 0)
  a = viewAidKey(k('t'), a)!
  assert.equal(a.actual, false)
  assert.equal(a.tile, 2)
  // with a modifier, or an unknown key, nothing happens
  assert.equal(viewAidKey(k('g', { metaKey: true }), a), null)
  assert.equal(viewAidKey(k('q'), a), null)
  // compare offers no tile or 100 %
  assert.equal(viewAidKey(k('t'), DEFAULT_AIDS, { tile: false, actual: false }), null)
  assert.equal(viewAidKey(k('1'), DEFAULT_AIDS, { tile: false, actual: false }), null)
})

test('Escape leaves 100 % and tile first, then lets the preview close', () => {
  assert.equal(escapeAids(DEFAULT_AIDS), null)
  assert.equal(escapeAids({ ...DEFAULT_AIDS, grey: true }), null)
  const out = escapeAids({ ...DEFAULT_AIDS, grey: true, actual: true })!
  assert.equal(out.actual, false)
  assert.equal(out.grey, true)
  assert.equal(escapeAids({ ...DEFAULT_AIDS, tile: 3 })!.tile, 0)
})

test('the keys are in the keymap for the preview and compare', () => {
  const keymap = readFileSync(new URL('../src/keymap.ts', import.meta.url), 'utf8')
  for (const does of ['greyscale (value check)', 'posterise', 'mirror on or off', 'grid: off', 'repeat tile', 'true 100 %']) assert.match(keymap, new RegExp(does.replace(/[()]/g, '\\$&')))
})

test('filters: greyscale, then posterise wins', () => {
  assert.equal(filterKind(DEFAULT_AIDS), null)
  assert.equal(filterKind({ ...DEFAULT_AIDS, grey: true }), 'grey')
  assert.equal(filterKind({ ...DEFAULT_AIDS, posterize: 3 }), 'post3')
  assert.equal(filterKind({ ...DEFAULT_AIDS, grey: true, posterize: 5 }), 'post5')
  assert.equal(posterizeTable(3), '0 0.5 1')
  assert.equal(posterizeTable(5), '0 0.25 0.5 0.75 1')
})

test('grid lines', () => {
  assert.deepEqual(gridLines({ grid: 'off', gridN: 6 }), [])
  assert.deepEqual(gridLines({ grid: 'thirds', gridN: 6 }), [1 / 3, 2 / 3])
  assert.deepEqual(gridLines({ grid: '4x4', gridN: 6 }), [0.25, 0.5, 0.75])
  assert.equal(gridLines({ grid: 'custom', gridN: 6 }).length, 5)
  assert.equal(gridLines({ grid: 'custom', gridN: 99 }).length, 23)
  assert.equal(clampGridN(1), 2)
  assert.equal(clampGridN(Number.NaN), DEFAULT_AIDS.gridN)
})

test('the grid sits on the contained picture, not the letterbox', () => {
  assert.deepEqual(containedRect(1000, 500, 2000, 2000), { x: 250, y: 0, w: 500, h: 500 })
  assert.deepEqual(containedRect(400, 800, 4000, 2000), { x: 0, y: 300, w: 400, h: 200 })
})

test('tiles cover the box with no gap', () => {
  const covered = (n: number, mode: Parameters<typeof tileCells>[1]) => {
    const cells = tileCells(n, mode)
    // sample the box on a fine lattice: every point is inside some tile
    for (let x = 0.01; x < n; x += 0.1)
      for (let y = 0.01; y < n; y += 0.1) assert.ok(cells.some((c) => x >= c.x && x < c.x + 1 && y >= c.y && y < c.y + 1), `${mode} ${n}: gap at ${x},${y}`)
    return cells
  }
  assert.equal(covered(2, 'straight').length, 4)
  assert.equal(covered(3, 'straight').length, 9)
  const drop = covered(3, 'half-drop')
  assert.ok(drop.some((c) => c.y === 0.5) && drop.some((c) => c.y === -0.5))
  const brick = covered(3, 'half-brick')
  assert.ok(brick.some((c) => c.x === 0.5) && brick.some((c) => c.x === -0.5))
})

test('true 100 % accounts for the device pixel ratio and the drawn copy', () => {
  assert.deepEqual(actualSize(4000, 3000, 2), { w: 2000, h: 1500 })
  assert.deepEqual(actualSize(4000, 3000, 1), { w: 4000, h: 3000 })
  assert.deepEqual(actualSize(300, 200, 1.5), { w: 200, h: 200 / 1.5 })
  assert.deepEqual(actualSize(300, 200, 0), { w: 300, h: 200 })
  // a TIFF the browser can't draw: the 2048 preview, 1:1
  assert.deepEqual(actualSource(6000, 4000, false, 2048), { w: 2048, h: 1365, original: false })
  assert.deepEqual(actualSource(6000, 4000, true, 2048), { w: 6000, h: 4000, original: true })
  assert.deepEqual(actualSource(1000, 800, false, 2048), { w: 1000, h: 800, original: true })
  // compare: the zoom that makes a fitted picture 1:1
  assert.equal(trueZoom(4000, 1000, 2), 2)
  assert.equal(trueZoom(4000, 0, 2), null)
})

test('summary and the store', () => {
  assert.equal(anyAid(DEFAULT_AIDS), false)
  assert.deepEqual(aidsSummary({ ...DEFAULT_AIDS, posterize: 3, mirror: true, grid: 'thirds', tile: 2, tileMode: 'half-drop' }), [
    '3 values',
    'Mirrored',
    'Thirds',
    'Repeat 2 × 2, half-drop',
  ])
  assert.deepEqual(aidsSummary({ ...DEFAULT_AIDS, tile: 2 }, { tile: false, actual: false }), [])
  const s = createAidsStore()
  let calls = 0
  const off = s.subscribe(() => calls++)
  s.set((a) => ({ ...a, grey: true, gridN: 500 }))
  assert.equal(s.get().grey, true)
  assert.equal(s.get().gridN, 24)
  s.reset()
  assert.equal(anyAid(s.get()), false)
  off()
  s.set({ ...DEFAULT_AIDS, mirror: true })
  assert.equal(calls, 2)
})

test('nothing in the aids writes or fetches', () => {
  for (const f of ['../src/viewAids.ts', '../src/components/viewaids/ViewAids.tsx']) {
    const src = readFileSync(new URL(f, import.meta.url), 'utf8')
    assert.doesNotMatch(src, /\bfetch\(|request\(|api\.|localStorage/, f)
  }
})
