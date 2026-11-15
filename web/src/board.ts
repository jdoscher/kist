// Boards (Milestone 3): the document shape and the canvas geometry, with
// no React (tests/board.test.ts). The editor draws nodes as absolutely
// positioned DOM elements inside one transformed "world" element, so every
// number here is in world units unless it says screen.
//
// A node is {id, type, x, y, w, h, rot, z, item_id?, crop?, text?, style?,
// children?}; an edge is {id, from, to, label?, style?}. The server owns the
// shape (kist/services/boards.py) and bumps `version` on every save.

export type NodeType = 'item' | 'note' | 'text' | 'shape' | 'group' | 'link' | 'color'

export interface Crop {
  x: number
  y: number
  w: number
  h: number
}

export interface NodeStyle {
  /** fill: note paper, shape fill, swatch colour, group tint */
  bg?: string
  /** text colour */
  color?: string
  shape?: 'rect' | 'ellipse' | 'diamond'
  fontSize?: number
  /** link nodes */
  url?: string
  [key: string]: unknown
}

export interface BoardNode {
  id: string
  type: NodeType
  x: number
  y: number
  w: number
  h: number
  rot: number
  z: number
  item_id?: string
  crop?: Crop | null
  text?: string
  style?: NodeStyle
  children?: string[]
}

export interface EdgeStyle {
  color?: string
  dash?: boolean
  arrow?: 'end' | 'both' | 'none'
  [key: string]: unknown
}

export interface BoardEdge {
  id: string
  from: string
  to: string
  label?: string
  style?: EdgeStyle
}

export interface Canvas {
  bg: string
  grid: number
  [key: string]: unknown
}

/** What the editor edits (and what PUT sends, plus base_version). */
export interface BoardContent {
  name: string
  canvas: Canvas
  nodes: BoardNode[]
  edges: BoardEdge[]
}

/** GET /api/boards/{id}: board.json plus server facts. */
export interface BoardDoc extends BoardContent {
  id: string
  version: number
  created_at: string
  modified_at: string
  state?: 'active' | 'trashed'
  item_count?: number
  missing_item_ids?: string[]
  thumb_url?: string
}

/** GET /api/boards rows. */
export interface BoardSummary {
  id: string
  name: string
  version: number
  item_count: number
  created_at: string
  modified_at: string
  deleted_at?: string | null
  thumb_url: string
}

/** PATCH /api/boards/{id} body. */
export interface BoardPatch {
  base_version?: number
  name?: string
  canvas?: Partial<Canvas>
  add: BoardNode[]
  update: (Partial<BoardNode> & { id: string } & Record<string, unknown>)[]
  remove: string[]
  edges_add: BoardEdge[]
  edges_update: (Partial<BoardEdge> & { id: string } & Record<string, unknown>)[]
  edges_remove: string[]
}

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface Viewport {
  /** screen offset of world (0, 0) */
  x: number
  y: number
  zoom: number
}

export const ZOOM_MIN = 0.05
export const ZOOM_MAX = 8

// ---------- ids ----------

let counter = 0
/** A node or edge id the server accepts (letters and digits, < 64). */
export function newNodeId(now = Date.now(), rand = Math.random): string {
  counter = (counter + 1) % 1296
  return `n${now.toString(36)}${counter.toString(36).padStart(2, '0')}${Math.floor(rand() * 36 ** 4)
    .toString(36)
    .padStart(4, '0')}`
}

// ---------- viewport ----------

export function clampZoom(z: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z))
}

export function toWorld(vp: Viewport, sx: number, sy: number): { x: number; y: number } {
  return { x: (sx - vp.x) / vp.zoom, y: (sy - vp.y) / vp.zoom }
}

export function toScreen(vp: Viewport, wx: number, wy: number): { x: number; y: number } {
  return { x: wx * vp.zoom + vp.x, y: wy * vp.zoom + vp.y }
}

/** Zoom by `factor` keeping the world point under screen (sx, sy) still. */
export function zoomAt(vp: Viewport, sx: number, sy: number, factor: number): Viewport {
  const zoom = clampZoom(vp.zoom * factor)
  const w = toWorld(vp, sx, sy)
  return { zoom, x: sx - w.x * zoom, y: sy - w.y * zoom }
}

/** The viewport that shows `r` centred in a view of viewW × viewH. */
export function fitRect(r: Rect | null, viewW: number, viewH: number, pad = 48, maxZoom = 1): Viewport {
  if (!r || r.w <= 0 || r.h <= 0) return { x: pad, y: pad, zoom: 1 }
  const zoom = clampZoom(Math.min((viewW - pad * 2) / r.w, (viewH - pad * 2) / r.h, maxZoom))
  return { zoom, x: (viewW - r.w * zoom) / 2 - r.x * zoom, y: (viewH - r.h * zoom) / 2 - r.y * zoom }
}

/** Two-finger gesture: the viewport that keeps the world point under the
 * old midpoint under the new one, scaled by the change in finger distance. */
export function pinch(
  vp: Viewport,
  a0: { x: number; y: number },
  b0: { x: number; y: number },
  a1: { x: number; y: number },
  b1: { x: number; y: number },
): Viewport {
  const d0 = Math.hypot(b0.x - a0.x, b0.y - a0.y) || 1
  const d1 = Math.hypot(b1.x - a1.x, b1.y - a1.y) || 1
  const m0 = { x: (a0.x + b0.x) / 2, y: (a0.y + b0.y) / 2 }
  const m1 = { x: (a1.x + b1.x) / 2, y: (a1.y + b1.y) / 2 }
  const w = toWorld(vp, m0.x, m0.y)
  const zoom = clampZoom(vp.zoom * (d1 / d0))
  return { zoom, x: m1.x - w.x * zoom, y: m1.y - w.y * zoom }
}

/** A wheel event: pinch on a trackpad (ctrlKey) or ⌘/Ctrl+wheel zooms,
 * a plain wheel or two-finger scroll pans. */
export function wheel(
  vp: Viewport,
  e: { deltaX: number; deltaY: number; ctrlKey: boolean; metaKey: boolean; deltaMode?: number },
  sx: number,
  sy: number,
): Viewport {
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1
  // one wheel notch (~100) is about ×1.4; a fast flick is capped at ×2
  if (e.ctrlKey || e.metaKey) return zoomAt(vp, sx, sy, Math.exp(Math.max(-0.7, Math.min(0.7, (-e.deltaY * unit) / 300))))
  return { ...vp, x: vp.x - e.deltaX * unit, y: vp.y - e.deltaY * unit }
}

// ---------- geometry ----------

export function snap(v: number, grid: number): number {
  return grid > 0 ? Math.round(v / grid) * grid : v
}

export function rectOf(n: Pick<BoardNode, 'x' | 'y' | 'w' | 'h'>): Rect {
  return { x: n.x, y: n.y, w: n.w, h: n.h }
}

export function union(rects: Rect[]): Rect | null {
  if (!rects.length) return null
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const r of rects) {
    x0 = Math.min(x0, r.x)
    y0 = Math.min(y0, r.y)
    x1 = Math.max(x1, r.x + r.w)
    y1 = Math.max(y1, r.y + r.h)
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

export function boundsOf(nodes: BoardNode[]): Rect | null {
  return union(nodes.map(rectOf))
}

/** Normalise a rectangle dragged in any direction. */
export function normRect(x0: number, y0: number, x1: number, y1: number): Rect {
  return { x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) }
}

export function intersects(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

export function contains(r: Rect, x: number, y: number): boolean {
  return x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h
}

/** Draw order: by z, then document order (ties). */
export function byZ(nodes: BoardNode[]): BoardNode[] {
  return nodes.map((n, i) => ({ n, i })).sort((a, b) => a.n.z - b.n.z || a.i - b.i).map((x) => x.n)
}

/** The topmost node under a world point. Groups (frames) only answer
 * where no other node is, so clicking a child inside a frame picks the
 * child. */
export function hitTest(nodes: BoardNode[], x: number, y: number): BoardNode | null {
  const order = byZ(nodes).reverse()
  const hit = order.find((n) => n.type !== 'group' && contains(rectOf(n), x, y))
  return hit ?? order.find((n) => n.type === 'group' && contains(rectOf(n), x, y)) ?? null
}

/** Nodes a marquee touches. A group counts only when wholly inside. */
export function nodesInRect(nodes: BoardNode[], r: Rect): string[] {
  return nodes
    .filter((n) => {
      if (n.type !== 'group') return intersects(rectOf(n), r)
      return n.x >= r.x && n.y >= r.y && n.x + n.w <= r.x + r.w && n.y + n.h <= r.y + r.h
    })
    .map((n) => n.id)
}

/** The ids plus every group's children (recursively): what moves together. */
export function withChildren(nodes: BoardNode[], ids: Iterable<string>): Set<string> {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const out = new Set<string>()
  const stack = [...ids]
  while (stack.length) {
    const id = stack.pop() as string
    if (out.has(id)) continue
    out.add(id)
    for (const c of byId.get(id)?.children ?? []) stack.push(c)
  }
  return out
}

/** Move nodes (and their groups' children) by dx, dy. With a grid, the
 * moved selection's top-left corner lands on the grid. */
export function moveNodes(nodes: BoardNode[], ids: Iterable<string>, dx: number, dy: number, grid = 0): BoardNode[] {
  const set = withChildren(nodes, ids)
  if (grid > 0) {
    const b = boundsOf(nodes.filter((n) => set.has(n.id)))
    if (b) {
      dx = snap(b.x + dx, grid) - b.x
      dy = snap(b.y + dy, grid) - b.y
    }
  }
  if (!dx && !dy) return nodes
  return nodes.map((n) => (set.has(n.id) ? { ...n, x: n.x + dx, y: n.y + dy } : n))
}

export type Handle = 'nw' | 'ne' | 'sw' | 'se'

/** Resize from a corner handle. `keepAspect` (item nodes, or Shift) keeps
 * the shape; sizes never go below `min`. */
export function resizeRect(r: Rect, handle: Handle, dx: number, dy: number, keepAspect = false, min = 16, grid = 0): Rect {
  const left = handle === 'nw' || handle === 'sw'
  const top = handle === 'nw' || handle === 'ne'
  let w = r.w + (left ? -dx : dx)
  let h = r.h + (top ? -dy : dy)
  if (grid > 0) {
    // snap the moving corner
    const cx = left ? r.x + r.w - w : r.x + w
    const cy = top ? r.y + r.h - h : r.y + h
    const sx = snap(cx, grid)
    const sy = snap(cy, grid)
    w = left ? r.x + r.w - sx : sx - r.x
    h = top ? r.y + r.h - sy : sy - r.y
  }
  if (keepAspect && r.w > 0 && r.h > 0) {
    const aspect = r.w / r.h
    if (Math.abs(w / r.w) > Math.abs(h / r.h)) h = w / aspect
    else w = h * aspect
    if (w < min) {
      w = min
      h = min / aspect
    }
    if (h < min) {
      h = min
      w = min * aspect
    }
  } else {
    w = Math.max(min, w)
    h = Math.max(min, h)
  }
  return { x: left ? r.x + r.w - w : r.x, y: top ? r.y + r.h - h : r.y, w, h }
}

// ---------- align, distribute, order ----------

export type AlignMode = 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom'

/** Align the selected nodes to the selection's bounds. */
export function align(nodes: BoardNode[], ids: string[], mode: AlignMode): BoardNode[] {
  const sel = nodes.filter((n) => ids.includes(n.id))
  const b = boundsOf(sel)
  if (!b || sel.length < 2) return nodes
  const target = (n: BoardNode): { x: number; y: number } => {
    switch (mode) {
      case 'left':
        return { x: b.x, y: n.y }
      case 'center':
        return { x: b.x + (b.w - n.w) / 2, y: n.y }
      case 'right':
        return { x: b.x + b.w - n.w, y: n.y }
      case 'top':
        return { x: n.x, y: b.y }
      case 'middle':
        return { x: n.x, y: b.y + (b.h - n.h) / 2 }
      case 'bottom':
        return { x: n.x, y: b.y + b.h - n.h }
    }
  }
  let out = nodes
  for (const n of sel) {
    const t = target(n)
    out = moveNodes(out, [n.id], t.x - n.x, t.y - n.y)
  }
  return out
}

/** Equal gaps between the selected nodes along an axis (3 or more), the
 * first and last staying put. */
export function distribute(nodes: BoardNode[], ids: string[], axis: 'h' | 'v'): BoardNode[] {
  const sel = nodes.filter((n) => ids.includes(n.id))
  if (sel.length < 3) return nodes
  const pos = (n: BoardNode) => (axis === 'h' ? n.x : n.y)
  const size = (n: BoardNode) => (axis === 'h' ? n.w : n.h)
  const sorted = [...sel].sort((a, b) => pos(a) - pos(b))
  const first = sorted[0]
  const last = sorted[sorted.length - 1]
  const span = pos(last) + size(last) - pos(first)
  const total = sorted.reduce((s, n) => s + size(n), 0)
  const gap = (span - total) / (sorted.length - 1)
  let cursor = pos(first)
  let out = nodes
  for (const n of sorted) {
    const d = cursor - pos(n)
    if (n !== first && n !== last && Math.abs(d) > 1e-9) out = moveNodes(out, [n.id], axis === 'h' ? d : 0, axis === 'v' ? d : 0)
    cursor += size(n) + gap
  }
  return out
}

export type OrderMode = 'front' | 'back' | 'forward' | 'backward'

/** Change stacking order. Returns nodes with new z values (only the nodes
 * whose z changed differ). */
export function reorder(nodes: BoardNode[], ids: string[], mode: OrderMode): BoardNode[] {
  const set = new Set(ids)
  if (!set.size) return nodes
  const order = byZ(nodes)
  let next: BoardNode[]
  if (mode === 'front') next = [...order.filter((n) => !set.has(n.id)), ...order.filter((n) => set.has(n.id))]
  else if (mode === 'back') next = [...order.filter((n) => set.has(n.id)), ...order.filter((n) => !set.has(n.id))]
  else {
    next = [...order]
    const step = mode === 'forward' ? 1 : -1
    const idx = next.map((n, i) => (set.has(n.id) ? i : -1)).filter((i) => i >= 0)
    if (step > 0) idx.reverse()
    for (const i of idx) {
      const j = i + step
      if (j < 0 || j >= next.length || set.has(next[j].id)) continue
      ;[next[i], next[j]] = [next[j], next[i]]
    }
  }
  const z = new Map(next.map((n, i) => [n.id, i + 1]))
  return nodes.map((n) => (z.get(n.id) === n.z ? n : { ...n, z: z.get(n.id) as number }))
}

/** Where to put something new near (x, y): stepped down and right past any
 * node whose corner is already there, so pressing Add three times doesn't
 * stack three pictures exactly on top of each other. */
export function freeSpot(nodes: Pick<BoardNode, 'x' | 'y'>[], x: number, y: number, step = 32): { x: number; y: number } {
  let p = { x, y }
  for (let i = 0; i < 50 && nodes.some((n) => Math.abs(n.x - p.x) < step / 2 && Math.abs(n.y - p.y) < step / 2); i++) {
    p = { x: p.x + step, y: p.y + step }
  }
  return p
}

export function topZ(nodes: BoardNode[]): number {
  return nodes.reduce((m, n) => Math.max(m, n.z), 0)
}

// ---------- duplicate, group ----------

/** Copies of the nodes (with their group children) and the edges between
 * them, offset, on top. */
export function duplicate(
  content: Pick<BoardContent, 'nodes' | 'edges'>,
  ids: string[],
  offset = 24,
  makeId: () => string = () => newNodeId(),
): { nodes: BoardNode[]; edges: BoardEdge[]; newIds: string[] } {
  const set = withChildren(content.nodes, ids)
  const map = new Map<string, string>()
  for (const id of set) map.set(id, makeId())
  let z = topZ(content.nodes)
  const copies = byZ(content.nodes.filter((n) => set.has(n.id))).map((n) => ({
    ...structuredCloneSafe(n),
    id: map.get(n.id) as string,
    x: n.x + offset,
    y: n.y + offset,
    z: ++z,
    // every child of a copied group is copied too (withChildren)
    ...(n.children ? { children: n.children.map((c) => map.get(c)).filter((c): c is string => !!c) } : {}),
  }))
  const edges = content.edges
    .filter((e) => set.has(e.from) && set.has(e.to))
    .map((e) => ({ ...structuredCloneSafe(e), id: makeId(), from: map.get(e.from) as string, to: map.get(e.to) as string }))
  return {
    nodes: [...content.nodes, ...copies],
    edges: [...content.edges, ...edges],
    newIds: ids.map((id) => map.get(id) as string),
  }
}

function structuredCloneSafe<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T
}

export const GROUP_PAD = 24

/** Frame the nodes in a new group placed behind them. */
export function group(nodes: BoardNode[], ids: string[], id = newNodeId(), label = 'Group'): { nodes: BoardNode[]; id: string } | null {
  const sel = nodes.filter((n) => ids.includes(n.id))
  const b = boundsOf(sel)
  if (!b || sel.length < 1) return null
  // the frame goes just behind its lowest member: everything at or above
  // that moves up one
  const minZ = Math.min(...sel.map((n) => n.z))
  const shifted = nodes.map((n) => (n.z >= minZ ? { ...n, z: n.z + 1 } : n))
  const frame: BoardNode = {
    id,
    type: 'group',
    x: b.x - GROUP_PAD,
    y: b.y - GROUP_PAD - 20,
    w: b.w + GROUP_PAD * 2,
    h: b.h + GROUP_PAD * 2 + 20,
    rot: 0,
    z: minZ,
    text: label,
    children: sel.map((n) => n.id),
    style: {},
  }
  return { nodes: [...shifted, frame], id }
}

/** Remove the group frames, keeping their children. */
export function ungroup(nodes: BoardNode[], ids: string[]): BoardNode[] {
  const set = new Set(nodes.filter((n) => ids.includes(n.id) && n.type === 'group').map((n) => n.id))
  return nodes.filter((n) => !set.has(n.id))
}

/** Remove nodes, their edges, and their ids from group children. */
export function removeNodes(content: Pick<BoardContent, 'nodes' | 'edges'>, ids: Iterable<string>) {
  const set = new Set(ids)
  return {
    nodes: content.nodes
      .filter((n) => !set.has(n.id))
      .map((n) => (n.children?.some((c) => set.has(c)) ? { ...n, children: n.children.filter((c) => !set.has(c)) } : n)),
    edges: content.edges.filter((e) => !set.has(e.from) && !set.has(e.to) && !set.has(e.id)),
  }
}

// ---------- edges ----------

/** Where the line from a's centre to b's centre leaves a's box. */
export function borderPoint(r: Rect, toward: { x: number; y: number }): { x: number; y: number } {
  const cx = r.x + r.w / 2
  const cy = r.y + r.h / 2
  const dx = toward.x - cx
  const dy = toward.y - cy
  if (!dx && !dy) return { x: cx, y: cy }
  const sx = dx ? r.w / 2 / Math.abs(dx) : Infinity
  const sy = dy ? r.h / 2 / Math.abs(dy) : Infinity
  const s = Math.min(sx, sy)
  return { x: cx + dx * s, y: cy + dy * s }
}

/** An edge's two ends, on the borders of the boxes it joins. */
export function edgeLine(a: Rect, b: Rect): { x1: number; y1: number; x2: number; y2: number } {
  const ca = { x: a.x + a.w / 2, y: a.y + a.h / 2 }
  const cb = { x: b.x + b.w / 2, y: b.y + b.h / 2 }
  const p = borderPoint(a, cb)
  const q = borderPoint(b, ca)
  return { x1: p.x, y1: p.y, x2: q.x, y2: q.y }
}

// ---------- crop ----------

/** CSS (percentages) that shows only the crop of an image filling a node. */
export function cropStyle(crop: Crop | null | undefined): { width: string; height: string; left: string; top: string } {
  const c = crop ?? { x: 0, y: 0, w: 1, h: 1 }
  return {
    width: `${100 / c.w}%`,
    height: `${100 / c.h}%`,
    left: `${(-c.x / c.w) * 100}%`,
    top: `${(-c.y / c.h) * 100}%`,
  }
}

/** Clamp a crop into the image (fractions 0-1, at least 2% each way). */
export function clampCrop(c: Crop): Crop {
  const w = Math.min(1, Math.max(0.02, c.w))
  const h = Math.min(1, Math.max(0.02, c.h))
  const x = Math.min(1 - w, Math.max(0, c.x))
  const y = Math.min(1 - h, Math.max(0, c.y))
  return { x, y, w, h }
}

/** A node's new height after cropping, keeping its width: the visible part
 * keeps the image's proportions. */
export function croppedHeight(nodeW: number, imgW: number, imgH: number, crop: Crop): number {
  if (!imgW || !imgH) return nodeW
  return (nodeW * (imgH * crop.h)) / (imgW * crop.w)
}

// ---------- diff for PATCH ----------

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** The fields of `next` that differ from `prev` (null for keys removed),
 * or null when nothing changed. */
function changed<T extends { id: string }>(prev: T, next: T): Record<string, unknown> | null {
  const out: Record<string, unknown> = {}
  const p = prev as unknown as Record<string, unknown>
  const n = next as unknown as Record<string, unknown>
  for (const k of new Set([...Object.keys(p), ...Object.keys(n)])) {
    if (k === 'id') continue
    if (!(k in n) || n[k] === undefined) {
      if (k in p && p[k] !== undefined && p[k] !== null) out[k] = null
    } else if (!same(p[k], n[k])) out[k] = n[k]
  }
  return Object.keys(out).length ? { id: next.id, ...out } : null
}

/** The PATCH ops that turn `base` (what the server has) into `next`. */
export function diffContent(base: BoardContent, next: BoardContent): BoardPatch {
  const patch: BoardPatch = { add: [], update: [], remove: [], edges_add: [], edges_update: [], edges_remove: [] }
  if (base.name !== next.name) patch.name = next.name
  if (!same(base.canvas, next.canvas)) patch.canvas = next.canvas
  const before = new Map(base.nodes.map((n) => [n.id, n]))
  const after = new Map(next.nodes.map((n) => [n.id, n]))
  for (const n of next.nodes) {
    const b = before.get(n.id)
    if (!b) patch.add.push(n)
    else {
      const c = changed(b, n)
      if (c) patch.update.push(c as BoardPatch['update'][number])
    }
  }
  for (const n of base.nodes) if (!after.has(n.id)) patch.remove.push(n.id)
  const eBefore = new Map(base.edges.map((e) => [e.id, e]))
  const eAfter = new Map(next.edges.map((e) => [e.id, e]))
  for (const e of next.edges) {
    const b = eBefore.get(e.id)
    // an edge whose ends changed is replaced (the server checks the ends)
    if (!b || b.from !== e.from || b.to !== e.to) {
      if (b) patch.edges_remove.push(e.id)
      patch.edges_add.push(e)
    } else {
      const c = changed(b, e)
      if (c) patch.edges_update.push(c as BoardPatch['edges_update'][number])
    }
  }
  // edges of removed nodes go with them on the server; say so anyway
  const removedNodes = new Set(patch.remove)
  for (const e of base.edges)
    if (!eAfter.has(e.id) && !(removedNodes.has(e.from) || removedNodes.has(e.to))) patch.edges_remove.push(e.id)
  return patch
}

export function isEmptyPatch(p: BoardPatch): boolean {
  return (
    p.name === undefined &&
    p.canvas === undefined &&
    !p.add.length &&
    !p.update.length &&
    !p.remove.length &&
    !p.edges_add.length &&
    !p.edges_update.length &&
    !p.edges_remove.length
  )
}

/** Apply a patch locally (what the server does), for tests and for
 * replaying a save on top of a newer server copy. */
export function applyPatch(base: BoardContent, p: BoardPatch): BoardContent {
  const removed = new Set(p.remove)
  let nodes = base.nodes.filter((n) => !removed.has(n.id)).map((n) => (n.children ? { ...n, children: n.children.filter((c) => !removed.has(c)) } : n))
  const merge = <T extends object>(t: T, u: Record<string, unknown>): T => {
    const out = { ...t } as Record<string, unknown>
    for (const [k, v] of Object.entries(u)) {
      if (k === 'id') continue
      if (v === null) delete out[k]
      else out[k] = v
    }
    return out as T
  }
  const upd = new Map(p.update.map((u) => [u.id, u]))
  nodes = nodes.map((n) => (upd.has(n.id) ? merge(n, upd.get(n.id) as Record<string, unknown>) : n))
  nodes = [...nodes, ...p.add]
  const ids = new Set(nodes.map((n) => n.id))
  const eRemoved = new Set(p.edges_remove)
  const eUpd = new Map(p.edges_update.map((u) => [u.id, u]))
  let edges = base.edges
    .filter((e) => !eRemoved.has(e.id) && ids.has(e.from) && ids.has(e.to))
    .map((e) => (eUpd.has(e.id) ? merge(e, eUpd.get(e.id) as Record<string, unknown>) : e))
  edges = [...edges, ...p.edges_add]
  return {
    name: p.name ?? base.name,
    canvas: p.canvas ? ({ ...base.canvas, ...p.canvas } as Canvas) : base.canvas,
    nodes,
    edges,
  }
}

export function contentOf(doc: BoardContent): BoardContent {
  return { name: doc.name, canvas: doc.canvas, nodes: doc.nodes, edges: doc.edges }
}

// ---------- undo within the session ----------

export interface History {
  past: BoardContent[]
  future: BoardContent[]
}

export const HISTORY_MAX = 100

export function pushHistory(h: History, before: BoardContent): History {
  return { past: [...h.past, before].slice(-HISTORY_MAX), future: [] }
}

export function undo(h: History, current: BoardContent): { history: History; content: BoardContent } | null {
  const prev = h.past.at(-1)
  if (!prev) return null
  return { history: { past: h.past.slice(0, -1), future: [current, ...h.future] }, content: prev }
}

export function redo(h: History, current: BoardContent): { history: History; content: BoardContent } | null {
  const next = h.future[0]
  if (!next) return null
  return { history: { past: [...h.past, current], future: h.future.slice(1) }, content: next }
}

// ---------- new nodes ----------

export const NOTE_COLOURS = ['#fff3b0', '#ffd6cc', '#d4f0d0', '#d6e6ff', '#ead9ff', '#f2f0eb'] as const
export const SWATCHES = ['#c2410c', '#15803d', '#1d4ed8', '#7e22ce', '#1c1b19', '#f5f5f4'] as const
/** canvas.bg "theme": the board sits on the app theme's image canvas, so
 * it matches the grid in every theme (the default for new boards since the
 * design pass; older boards keep the colour they have). */
export const THEME_BG = 'theme'
export const CANVAS_BGS: { bg: string; name: string }[] = [
  { bg: THEME_BG, name: 'Theme' },
  { bg: '#f5f5f4', name: 'Paper' },
  { bg: '#767676', name: 'Mid grey' },
  { bg: '#1c1b19', name: 'Dark' },
]

/** The CSS background of a board's canvas. */
export function canvasCss(bg: string): string {
  return bg === THEME_BG ? 'var(--canvas)' : bg
}

/** Default ink for text and arrows that have no colour of their own: the
 * theme's canvas text on a theme board, dark ink on a chosen colour. */
export function canvasInk(bg: string, kind: 'text' | 'line' = 'text'): string {
  if (bg === THEME_BG) return kind === 'text' ? 'var(--canvas-text)' : 'var(--canvas-dim)'
  return kind === 'text' ? '#1c1b19' : '#57524a'
}

export function newNode(type: NodeType, x: number, y: number, z: number, extra: Partial<BoardNode> = {}): BoardNode {
  const base: Record<NodeType, Partial<BoardNode>> = {
    item: { w: 240, h: 240 },
    note: { w: 200, h: 160, text: '', style: { bg: NOTE_COLOURS[0], color: '#1c1b19' } },
    // no colour: text follows the board (canvasInk), so it reads on any theme
    text: { w: 280, h: 56, text: 'Text', style: { fontSize: 28 } },
    shape: { w: 200, h: 140, style: { bg: '#d6e6ff', shape: 'rect' } },
    color: { w: 120, h: 140, style: { bg: SWATCHES[0] } },
    link: { w: 260, h: 72, text: '', style: { url: '' } },
    group: { w: 400, h: 300, text: 'Group', children: [], style: {} },
  }
  return { id: newNodeId(), type, x, y, rot: 0, z, w: 200, h: 200, ...base[type], ...extra } as BoardNode
}

/** Item nodes for dropped items: a row starting at (x, y), 240 wide (or
 * 240 tall for portraits), snapped. */
export function itemNodes(
  items: { id: string; width: number | null; height: number | null }[],
  x: number,
  y: number,
  z: number,
  grid = 0,
): BoardNode[] {
  let cx = x
  return items.map((it, i) => {
    const aspect = it.width && it.height ? it.width / it.height : 1
    const w = aspect >= 1 ? 240 : 240 * aspect
    const h = aspect >= 1 ? 240 / aspect : 240
    const n = newNode('item', snap(cx, grid), snap(y, grid), z + i + 1, { item_id: it.id, w, h })
    cx += w + 20
    return n
  })
}

/** Readable one-line description of a node, for the list view and labels. */
export function nodeLabel(n: BoardNode, itemName?: (id: string) => string | undefined): string {
  const text = (n.text ?? '').trim().replace(/\s+/g, ' ')
  const short = text.length > 60 ? `${text.slice(0, 57)}…` : text
  switch (n.type) {
    case 'item':
      return `Image ${itemName?.(n.item_id ?? '') ?? '(missing item)'}`
    case 'note':
      return short ? `Note: ${short}` : 'Empty note'
    case 'text':
      return short ? `Text: ${short}` : 'Empty text'
    case 'shape':
      return `${n.style?.shape === 'ellipse' ? 'Ellipse' : n.style?.shape === 'diamond' ? 'Diamond' : 'Rectangle'}${short ? `: ${short}` : ''}`
    case 'color':
      return `Colour swatch ${n.style?.bg ?? ''}`.trim()
    case 'link':
      return `Link ${n.style?.url || short || ''}`.trim()
    case 'group':
      return `Group${short && short !== 'Group' ? ` “${short}”` : ''} (${n.children?.length ?? 0} inside)`
  }
}

/** Where a key press landed, for the board's shortcuts: the canvas or its
 * list/inspector (or nowhere: the page), the board's other chrome (the
 * toolbar's buttons), a text field, or somewhere else on screen. */
export type BoardKeyPlace = 'board' | 'chrome' | 'text' | 'outside'

/** Whether the board takes a key. On the canvas, every board key; on the
 * toolbar (focus stays on the last button pressed) the ⌘ shortcuts too, so
 * ⌘A, ⌘Z, ⌘D and ⌘G work without clicking the canvas first, and Escape
 * clears the selection; never in a text field or outside the board. */
export function boardTakesKey(place: BoardKeyPlace, mod: boolean, key: string): boolean {
  if (place === 'board') return true
  if (place !== 'chrome') return false
  if (key === 'Escape') return true
  if (!mod) return false
  return ['a', 'z', 'y', 'd', 'g'].includes(key.toLowerCase())
}

/** The board list's caption: editing words only where the board can be
 * edited (not on a phone or with a read-only token). */
export function boardListCaption(n: number, canEdit: boolean): string {
  const things = `${n} thing${n === 1 ? '' : 's'} on the board, top first.`
  return canEdit ? `${things} Select one to edit its position, size, colour and text in the panel beside.` : things
}
