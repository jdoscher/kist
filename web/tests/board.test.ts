// Board geometry and saving. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  boardTakesKey,
  align,
  applyPatch,
  borderPoint,
  clampCrop,
  cropStyle,
  croppedHeight,
  diffContent,
  distribute,
  duplicate,
  edgeLine,
  fitRect,
  group,
  hitTest,
  isEmptyPatch,
  itemNodes,
  moveNodes,
  nodesInRect,
  pinch,
  pushHistory,
  redo,
  removeNodes,
  reorder,
  resizeRect,
  snap,
  toScreen,
  toWorld,
  undo,
  ungroup,
  wheel,
  zoomAt,
  type BoardContent,
  type BoardNode,
  freeSpot,
} from '../src/board.ts'

const n = (id: string, x: number, y: number, w = 100, h = 100, z = 1, extra: Partial<BoardNode> = {}): BoardNode => ({
  id,
  type: 'note',
  x,
  y,
  w,
  h,
  rot: 0,
  z,
  ...extra,
})

const near = (a: number, b: number, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} ≈ ${b}`)

test('screen and world coordinates round-trip', () => {
  const vp = { x: 120, y: -40, zoom: 2.5 }
  const w = toWorld(vp, 300, 200)
  const s = toScreen(vp, w.x, w.y)
  near(s.x, 300)
  near(s.y, 200)
})

test('zooming at a point keeps that point still, within limits', () => {
  const vp = { x: 10, y: 20, zoom: 1 }
  const before = toWorld(vp, 400, 300)
  const z = zoomAt(vp, 400, 300, 2)
  assert.equal(z.zoom, 2)
  const after = toWorld(z, 400, 300)
  near(after.x, before.x)
  near(after.y, before.y)
  assert.equal(zoomAt(vp, 0, 0, 1000).zoom, 8)
  assert.equal(zoomAt(vp, 0, 0, 0.0001).zoom, 0.05)
})

test('wheel pans, ctrl/⌘+wheel (trackpad pinch) zooms', () => {
  const vp = { x: 0, y: 0, zoom: 1 }
  assert.deepEqual(wheel(vp, { deltaX: 5, deltaY: 30, ctrlKey: false, metaKey: false }, 0, 0), { x: -5, y: -30, zoom: 1 })
  assert.ok(wheel(vp, { deltaX: 0, deltaY: -100, ctrlKey: true, metaKey: false }, 50, 50).zoom > 1)
  assert.ok(wheel(vp, { deltaX: 0, deltaY: 100, ctrlKey: false, metaKey: true }, 50, 50).zoom < 1)
})

test('pinch zooms by finger distance around the moving midpoint', () => {
  const vp = { x: 0, y: 0, zoom: 1 }
  const out = pinch(vp, { x: 100, y: 100 }, { x: 200, y: 100 }, { x: 50, y: 100 }, { x: 250, y: 100 })
  assert.equal(out.zoom, 2)
  const w = toWorld(out, 150, 100)
  near(w.x, 150)
  near(w.y, 100)
})

test('fit centres the content and never zooms past 100%', () => {
  const vp = fitRect({ x: 0, y: 0, w: 200, h: 100 }, 1000, 800)
  assert.equal(vp.zoom, 1)
  near(vp.x, 400)
  near(vp.y, 350)
  const big = fitRect({ x: 1000, y: 0, w: 4000, h: 1000 }, 1000, 800, 0)
  near(big.zoom, 0.25)
  near(toScreen(big, 1000, 0).x, 0)
  assert.deepEqual(fitRect(null, 100, 100), { x: 48, y: 48, zoom: 1 })
})

test('snapping to a grid', () => {
  assert.equal(snap(37, 20), 40)
  assert.equal(snap(29, 20), 20)
  assert.equal(snap(37.3, 0), 37.3)
})

test('hit testing picks the topmost node, and a frame only around its children', () => {
  const nodes = [n('a', 0, 0, 100, 100, 1), n('b', 50, 50, 100, 100, 2), n('g', -50, -50, 400, 400, 5, { type: 'group', children: ['a', 'b'] })]
  assert.equal(hitTest(nodes, 75, 75)?.id, 'b')
  assert.equal(hitTest(nodes, 10, 10)?.id, 'a')
  assert.equal(hitTest(nodes, 300, 300)?.id, 'g')
  assert.equal(hitTest(nodes, 1000, 1000), null)
})

test('a marquee touches nodes, and takes a frame only when it is wholly inside', () => {
  const nodes = [n('a', 0, 0), n('b', 300, 0), n('g', -10, -10, 130, 130, 0, { type: 'group', children: ['a'] })]
  assert.deepEqual(nodesInRect(nodes, { x: 50, y: 50, w: 10, h: 10 }), ['a'])
  assert.deepEqual(nodesInRect(nodes, { x: -20, y: -20, w: 500, h: 200 }).sort(), ['a', 'b', 'g'])
})

test('moving a group moves its children; moving with a grid snaps the corner', () => {
  const nodes = [n('a', 10, 10), n('b', 500, 0), n('g', 0, 0, 200, 200, 0, { type: 'group', children: ['a'] })]
  const moved = moveNodes(nodes, ['g'], 5, 7)
  assert.deepEqual(
    moved.map((m) => [m.id, m.x, m.y]),
    [
      ['a', 15, 17],
      ['b', 500, 0],
      ['g', 5, 7],
    ],
  )
  const snapped = moveNodes([n('a', 3, 3)], ['a'], 15, 0, 20)
  assert.deepEqual([snapped[0].x, snapped[0].y], [20, 0])
})

test('resizing from a corner, with a minimum and kept proportions', () => {
  const r = { x: 0, y: 0, w: 200, h: 100 }
  assert.deepEqual(resizeRect(r, 'se', 50, 20), { x: 0, y: 0, w: 250, h: 120 })
  assert.deepEqual(resizeRect(r, 'nw', 50, 20), { x: 50, y: 20, w: 150, h: 80 })
  assert.deepEqual(resizeRect(r, 'se', -500, -500), { x: 0, y: 0, w: 16, h: 16 })
  const kept = resizeRect(r, 'se', 100, 0, true)
  assert.deepEqual([kept.w, kept.h], [300, 150])
  const grid = resizeRect(r, 'se', 13, 13, false, 16, 20)
  assert.deepEqual([grid.w, grid.h], [220, 120])
})

test('align to the selection bounds', () => {
  const nodes = [n('a', 0, 0, 100, 50), n('b', 200, 100, 50, 50), n('c', 1000, 1000)]
  const left = align(nodes, ['a', 'b'], 'left')
  assert.deepEqual(left.map((m) => m.x), [0, 0, 1000])
  const right = align(nodes, ['a', 'b'], 'right')
  assert.deepEqual(right.map((m) => m.x), [150, 200, 1000])
  const middle = align(nodes, ['a', 'b'], 'middle')
  assert.deepEqual(middle.map((m) => m.y), [50, 50, 1000])
  assert.equal(align(nodes, ['a'], 'left'), nodes, 'one node: nothing to align')
})

test('distribute leaves equal gaps between the ends', () => {
  const nodes = [n('a', 0, 0, 100), n('b', 130, 0, 50), n('c', 400, 0, 100)]
  const d = distribute(nodes, ['a', 'b', 'c'], 'h')
  // span 0..500, sizes 250, gaps (500-250)/2 = 125
  assert.deepEqual(d.map((m) => m.x), [0, 225, 400])
  assert.equal(distribute(nodes, ['a', 'b'], 'h'), nodes)
})

test('z-order: front, back, one step', () => {
  const nodes = [n('a', 0, 0, 10, 10, 1), n('b', 0, 0, 10, 10, 2), n('c', 0, 0, 10, 10, 3)]
  const z = (ns: BoardNode[]) => Object.fromEntries(ns.map((m) => [m.id, m.z]))
  assert.deepEqual(z(reorder(nodes, ['a'], 'front')), { a: 3, b: 1, c: 2 })
  assert.deepEqual(z(reorder(nodes, ['c'], 'back')), { a: 2, b: 3, c: 1 })
  assert.deepEqual(z(reorder(nodes, ['a'], 'forward')), { a: 2, b: 1, c: 3 })
  assert.deepEqual(z(reorder(nodes, ['a'], 'backward')), { a: 1, b: 2, c: 3 })
})

test('duplicate copies nodes, their group children and the edges between them', () => {
  let i = 0
  const content = {
    nodes: [n('a', 0, 0, 10, 10, 1), n('b', 50, 0, 10, 10, 2), n('g', -5, -5, 100, 30, 0, { type: 'group', children: ['a', 'b'] }), n('c', 500, 0)],
    edges: [
      { id: 'e1', from: 'a', to: 'b', label: 'x' },
      { id: 'e2', from: 'a', to: 'c' },
    ],
  }
  const out = duplicate(content, ['g'], 24, () => `new${++i}`)
  assert.equal(out.nodes.length, 7)
  const copyG = out.nodes.find((m) => m.id === out.newIds[0]) as BoardNode
  assert.equal(copyG.type, 'group')
  assert.equal(copyG.x, 19)
  assert.equal(copyG.children?.length, 2)
  assert.ok(copyG.children?.every((c) => c.startsWith('new')))
  assert.equal(out.edges.length, 3, 'only the edge inside the copy is copied')
  assert.equal(out.edges[2].label, 'x')
  assert.ok(Math.min(...out.nodes.filter((m) => m.id.startsWith('new')).map((m) => m.z)) > 2)
})

test('group frames the selection behind it; ungroup keeps the children', () => {
  const nodes = [n('a', 0, 0, 100, 100, 3), n('b', 200, 0, 100, 100, 5), n('c', 0, 0, 10, 10, 1)]
  const g = group(nodes, ['a', 'b'], 'g1')
  if (!g) throw new Error('no group')
  const frame = g.nodes.find((m) => m.id === 'g1') as BoardNode
  assert.deepEqual(frame.children, ['a', 'b'])
  assert.ok(frame.x < 0 && frame.x + frame.w > 300)
  assert.equal(frame.z, 3)
  assert.equal(g.nodes.find((m) => m.id === 'a')?.z, 4, 'members stay above their frame')
  assert.equal(g.nodes.find((m) => m.id === 'c')?.z, 1)
  assert.deepEqual(ungroup(g.nodes, ['g1']).map((m) => m.id), ['a', 'b', 'c'])
})

test('removing nodes drops their edges and group memberships', () => {
  const out = removeNodes(
    { nodes: [n('a', 0, 0), n('b', 0, 0), n('g', 0, 0, 1, 1, 0, { type: 'group', children: ['a', 'b'] })], edges: [{ id: 'e', from: 'a', to: 'b' }] },
    ['a'],
  )
  assert.deepEqual(out.nodes.find((m) => m.id === 'g')?.children, ['b'])
  assert.equal(out.edges.length, 0)
})

test('edges meet the borders of the boxes they join', () => {
  const a = { x: 0, y: 0, w: 100, h: 100 }
  const b = { x: 300, y: 0, w: 100, h: 100 }
  assert.deepEqual(edgeLine(a, b), { x1: 100, y1: 50, x2: 300, y2: 50 })
  assert.deepEqual(borderPoint(a, { x: 50, y: 1000 }), { x: 50, y: 100 })
  assert.deepEqual(borderPoint(a, { x: 50, y: 50 }), { x: 50, y: 50 })
})

test('crop shows the chosen part and keeps the node in proportion', () => {
  assert.deepEqual(cropStyle({ x: 0.25, y: 0, w: 0.5, h: 1 }), { width: '200%', height: '100%', left: '-50%', top: '0%' })
  assert.deepEqual(cropStyle(null), { width: '100%', height: '100%', left: '0%', top: '0%' })
  assert.deepEqual(clampCrop({ x: 0.9, y: -1, w: 0.5, h: 3 }), { x: 0.5, y: 0, w: 0.5, h: 1 })
  assert.equal(croppedHeight(200, 1000, 500, { x: 0, y: 0, w: 0.5, h: 1 }), 200)
})

test('dropped items are laid out in a row in their proportions', () => {
  const out = itemNodes(
    [
      { id: 'w', width: 2000, height: 1000 },
      { id: 't', width: 500, height: 1000 },
    ],
    100,
    40,
    7,
  )
  assert.deepEqual(
    out.map((m) => [m.item_id, m.x, m.y, m.w, m.h, m.z]),
    [
      ['w', 100, 40, 240, 120, 8],
      ['t', 360, 40, 120, 240, 9],
    ],
  )
})

const doc = (): BoardContent => ({
  name: 'Board',
  canvas: { bg: '#f5f5f4', grid: 0 },
  nodes: [n('a', 0, 0, 100, 100, 1, { text: 'hi', style: { bg: '#fff' } }), n('b', 200, 0), n('c', 400, 0)],
  edges: [
    { id: 'e1', from: 'a', to: 'b' },
    { id: 'e2', from: 'b', to: 'c', label: 'then' },
  ],
})

test('a save sends only what changed', () => {
  const base = doc()
  assert.ok(isEmptyPatch(diffContent(base, doc())))
  const next = doc()
  next.nodes[0] = { ...next.nodes[0], x: 10, text: undefined }
  next.nodes = next.nodes.filter((m) => m.id !== 'c')
  next.nodes.push(n('d', 0, 300))
  next.edges = [{ id: 'e1', from: 'a', to: 'b', label: 'new' }]
  next.name = 'Renamed'
  const p = diffContent(base, next)
  assert.equal(p.name, 'Renamed')
  assert.deepEqual(p.update, [{ id: 'a', x: 10, text: null }])
  assert.deepEqual(p.remove, ['c'])
  assert.deepEqual(p.add.map((m) => m.id), ['d'])
  assert.deepEqual(p.edges_update, [{ id: 'e1', label: 'new' }])
  assert.deepEqual(p.edges_remove, [], 'e2 goes with node c')
  assert.equal(p.canvas, undefined)
  // the server applying it gives the same document
  assert.deepEqual(applyPatch(base, p), JSON.parse(JSON.stringify(next)))
})

test('an edge whose ends change is replaced, and canvas changes are sent', () => {
  const base = doc()
  const next = doc()
  next.edges = [{ id: 'e1', from: 'a', to: 'c' }, base.edges[1]]
  next.canvas = { bg: '#1c1b19', grid: 20 }
  const p = diffContent(base, next)
  assert.deepEqual(p.edges_remove, ['e1'])
  assert.deepEqual(p.edges_add, [{ id: 'e1', from: 'a', to: 'c' }])
  assert.deepEqual(p.canvas, { bg: '#1c1b19', grid: 20 })
  assert.deepEqual(applyPatch(base, p).edges.map((e) => [e.id, e.from, e.to]).sort(), [
    ['e1', 'a', 'c'],
    ['e2', 'b', 'c'],
  ])
})

test('undo and redo within the session', () => {
  const a = doc()
  const b = { ...a, name: 'B' }
  const c = { ...a, name: 'C' }
  let h = pushHistory({ past: [], future: [] }, a)
  h = pushHistory(h, b)
  const u = undo(h, c)
  if (!u) throw new Error('no undo')
  assert.equal(u.content.name, 'B')
  const u2 = undo(u.history, u.content)
  assert.equal(u2?.content.name, 'Board')
  const r = redo(u2!.history, u2!.content)
  assert.equal(r?.content.name, 'B')
  assert.equal(undo({ past: [], future: [] }, a), null)
  // a new edit clears the redo stack
  assert.equal(pushHistory(u.history, u.content).future.length, 0)
})

test('adding again puts the new thing beside, not on top of, the last', () => {
  assert.deepEqual(freeSpot([], 100, 100), { x: 100, y: 100 })
  assert.deepEqual(freeSpot([{ x: 100, y: 100 }], 100, 100), { x: 132, y: 132 })
  assert.deepEqual(freeSpot([{ x: 100, y: 100 }, { x: 132, y: 132 }], 100, 100), { x: 164, y: 164 })
  assert.deepEqual(freeSpot([{ x: 400, y: 100 }], 100, 100), { x: 100, y: 100 })
})

test('board keys: the canvas takes all, the toolbar only the ⌘ shortcuts', () => {
  assert.ok(boardTakesKey('board', false, 'Delete') && boardTakesKey('board', true, 'a'))
  // focus left on a toolbar button: ⌘A still selects everything
  assert.ok(boardTakesKey('chrome', true, 'a') && boardTakesKey('chrome', true, 'Z'))
  assert.ok(!boardTakesKey('chrome', false, 'a')) // a plain key stays the button's
  assert.ok(boardTakesKey('chrome', false, 'Escape'))
  assert.ok(!boardTakesKey('chrome', true, 'c')) // ⌘C copies as usual
  assert.ok(!boardTakesKey('text', true, 'a') && !boardTakesKey('outside', true, 'a'))
})
