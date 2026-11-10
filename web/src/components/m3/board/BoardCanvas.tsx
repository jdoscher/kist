// The board canvas: DOM nodes in one transformed world element, arrows in
// an SVG, pointer gestures (select, marquee, move, resize, connect, pan,
// pinch) and wheel zoom. Everything here also has a keyboard or list-view
// way (keys.ts, BoardList, BoardInspector).

import { Link2, ImageOff } from 'lucide-react'
import { useEffect, useRef, useState, type RefObject } from 'react'
import { thumbUrl, type Item } from '../../../api'
import {
  byZ,
  cropStyle,
  edgeLine,
  hitTest,
  itemNodes,
  moveNodes,
  nodeLabel,
  nodesInRect,
  normRect,
  pinch,
  rectOf,
  resizeRect,
  toWorld,
  topZ,
  wheel,
  newNodeId,
  type BoardContent,
  type BoardNode,
  type Handle,
  type Rect,
} from '../../../board'
import { api3 } from '../../../creativeApi'
import { useAppState } from '../../../state'
import type { Editor } from './BoardEditor'

type Gesture =
  | { kind: 'pan'; sx: number; sy: number; vx: number; vy: number }
  | { kind: 'move'; wx: number; wy: number; before: BoardContent; ids: string[]; moved: boolean }
  | { kind: 'resize'; wx: number; wy: number; before: BoardContent; id: string; handle: Handle; rect: Rect }
  | { kind: 'marquee'; wx: number; wy: number; base: string[]; rect: Rect | null }
  | { kind: 'connect'; from: string; wx: number; wy: number }
  | { kind: 'pinch' }

const HANDLES: Handle[] = ['nw', 'ne', 'sw', 'se']

export function BoardCanvas({ ed, viewportRef }: { ed: Editor; viewportRef: RefObject<HTMLDivElement | null> }) {
  const { setPreviewId } = useAppState()
  const c = ed.content
  const vp = ed.vp
  const gesture = useRef<Gesture | null>(null)
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const [, render] = useState(0)
  const [dropHint, setDropHint] = useState(false)
  const edRef = useRef(ed)
  edRef.current = ed

  // the wheel needs a non-passive listener to stop the page scrolling
  useEffect(() => {
    const el = viewportRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = el.getBoundingClientRect()
      const cur = edRef.current
      cur.setVp(wheel(cur.vp, e, e.clientX - r.left, e.clientY - r.top))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [viewportRef])

  const local = (e: { clientX: number; clientY: number }) => {
    const r = viewportRef.current!.getBoundingClientRect()
    return { sx: e.clientX - r.left, sy: e.clientY - r.top }
  }
  const world = (e: { clientX: number; clientY: number }) => {
    const { sx, sy } = local(e)
    return toWorld(vp, sx, sy)
  }

  const panning = () => ed.tool === 'hand' || document.body.classList.contains('board-space') || !ed.canEdit

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement
    if (target.closest('textarea, input, a, .bnode-editing')) return
    viewportRef.current?.focus({ preventScroll: true })
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    e.currentTarget.setPointerCapture(e.pointerId)
    if (pointers.current.size === 2) {
      gesture.current = { kind: 'pinch' }
      return
    }
    const { sx, sy } = local(e)
    const w = toWorld(vp, sx, sy)
    if (e.button === 1 || panning()) {
      gesture.current = { kind: 'pan', sx, sy, vx: vp.x, vy: vp.y }
      return
    }
    const handle = target.closest<HTMLElement>('[data-handle]')
    if (handle) {
      const id = handle.dataset.node as string
      const n = c.nodes.find((x) => x.id === id)
      if (n) gesture.current = { kind: 'resize', wx: w.x, wy: w.y, before: c, id, handle: handle.dataset.handle as Handle, rect: rectOf(n) }
      return
    }
    const edgeEl = target.closest<SVGElement>('[data-edge]')
    if (edgeEl) {
      ed.setSel([])
      ed.setEdge(edgeEl.dataset.edge as string)
      return
    }
    const hit = hitTest(c.nodes, w.x, w.y)
    if (ed.tool === 'connect' && hit) {
      gesture.current = { kind: 'connect', from: hit.id, wx: w.x, wy: w.y }
      return
    }
    if (hit) {
      let ids = ed.sel
      if (e.shiftKey || e.metaKey || e.ctrlKey) {
        ids = ids.includes(hit.id) ? ids.filter((i) => i !== hit.id) : [...ids, hit.id]
        ed.setSel(ids)
        if (!ids.includes(hit.id)) return
      } else if (!ids.includes(hit.id)) {
        ids = [hit.id]
        ed.setSel(ids)
      }
      ed.announce(`Selected ${nodeLabel(hit, (i) => ed.items.get(i)?.filename)}`)
      gesture.current = { kind: 'move', wx: w.x, wy: w.y, before: c, ids, moved: false }
      return
    }
    ed.setEdge(null)
    gesture.current = { kind: 'marquee', wx: w.x, wy: w.y, base: e.shiftKey ? ed.sel : [], rect: null }
    if (!e.shiftKey) ed.setSel([])
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const prev = pointers.current.get(e.pointerId)
    if (!prev) return
    const g = gesture.current
    if (g?.kind === 'pinch' && pointers.current.size === 2) {
      const [idA, idB] = [...pointers.current.keys()]
      const a0 = pointers.current.get(idA)!
      const b0 = pointers.current.get(idB)!
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
      const a1 = pointers.current.get(idA)!
      const b1 = pointers.current.get(idB)!
      const r = viewportRef.current!.getBoundingClientRect()
      const off = (p: { x: number; y: number }) => ({ x: p.x - r.left, y: p.y - r.top })
      ed.setVp(pinch(vp, off(a0), off(b0), off(a1), off(b1)))
      return
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (!g) return
    const { sx, sy } = local(e)
    const w = toWorld(vp, sx, sy)
    if (g.kind === 'pan') ed.setVp({ ...vp, x: g.vx + sx - g.sx, y: g.vy + sy - g.sy })
    else if (g.kind === 'move') {
      const dx = w.x - g.wx
      const dy = w.y - g.wy
      if (!g.moved && Math.hypot(dx * vp.zoom, dy * vp.zoom) < 3) return
      g.moved = true
      ed.setLive({ ...g.before, nodes: moveNodes(g.before.nodes, g.ids, dx, dy, ed.grid) })
    } else if (g.kind === 'resize') {
      const n = g.before.nodes.find((x) => x.id === g.id)
      if (!n) return
      const keep = n.type === 'item' || n.type === 'color' ? !e.shiftKey : e.shiftKey
      const r = resizeRect(g.rect, g.handle, w.x - g.wx, w.y - g.wy, keep, 16, ed.grid)
      ed.setLive({ ...g.before, nodes: g.before.nodes.map((x) => (x.id === g.id ? { ...x, ...r } : x)) })
    } else if (g.kind === 'marquee') {
      g.rect = normRect(g.wx, g.wy, w.x, w.y)
      const hits = nodesInRect(c.nodes, g.rect)
      ed.setSel([...new Set([...g.base, ...hits])])
      render((n) => n + 1)
    } else if (g.kind === 'connect') {
      g.wx = w.x
      g.wy = w.y
      render((n) => n + 1)
    }
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId)
    const g = gesture.current
    if (g?.kind === 'pinch') {
      if (pointers.current.size === 0) gesture.current = null
      return
    }
    gesture.current = null
    if (!g) return
    if (g.kind === 'move' && g.moved) {
      ed.commit(ed.content, g.before)
      ed.announce(`Moved ${g.ids.length}`)
    } else if (g.kind === 'resize') ed.commit(ed.content, g.before)
    else if (g.kind === 'marquee') render((n) => n + 1)
    else if (g.kind === 'connect') {
      const w = world(e)
      const to = hitTest(c.nodes, w.x, w.y)
      if (to && to.id !== g.from) {
        ed.commit({ ...c, edges: [...c.edges, { id: newNodeId(), from: g.from, to: to.id, label: '', style: { arrow: 'end' } }] })
        ed.announce('Arrow added')
      }
      render((n) => n + 1)
    }
  }

  // ---------- drop items from the library panel ----------

  const onDragOver = (e: React.DragEvent) => {
    if (!ed.canEdit || !e.dataTransfer.types.includes('application/x-kist-items')) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setDropHint(true)
  }
  const onDrop = async (e: React.DragEvent) => {
    setDropHint(false)
    const raw = e.dataTransfer.getData('application/x-kist-items')
    if (!raw || !ed.canEdit) return
    e.preventDefault()
    const ids: string[] = JSON.parse(raw)
    const w = world(e)
    let list: Item[] = ids.map((i) => ed.items.get(i)).filter((x): x is Item => !!x)
    if (list.length < ids.length) {
      try {
        list = (await api3.itemsByIds(ids)).items
      } catch {
        return
      }
    }
    ed.addItems(list)
    const cur = edRef.current.content
    const nodes = itemNodes(list, w.x, w.y, topZ(cur.nodes), ed.grid)
    ed.commit({ ...cur, nodes: [...cur.nodes, ...nodes] })
    ed.setSel(nodes.map((n) => n.id))
    ed.announce(`Added ${nodes.length} item${nodes.length === 1 ? '' : 's'}`)
  }

  // ---------- keyboard: Tab walks the nodes ----------

  const order = byZ(c.nodes)
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab' || e.target !== viewportRef.current || !order.length) return
    const cur = ed.sel.length === 1 ? order.findIndex((n) => n.id === ed.sel[0]) : -1
    const next = cur + (e.shiftKey ? -1 : 1)
    if (next < 0 || next >= order.length) return // let Tab leave the canvas
    e.preventDefault()
    const n = order[next]
    ed.setSel([n.id])
    ed.announce(`${next + 1} of ${order.length}: ${nodeLabel(n, (i) => ed.items.get(i)?.filename)}, at ${Math.round(n.x)}, ${Math.round(n.y)}`)
    // keep it in view
    const { w, h } = ed.viewSize()
    const sx = n.x * vp.zoom + vp.x
    const sy = n.y * vp.zoom + vp.y
    if (sx < 0 || sy < 0 || sx > w - 40 || sy > h - 40) ed.setVp({ ...vp, x: w / 2 - (n.x + n.w / 2) * vp.zoom, y: h / 2 - (n.y + n.h / 2) * vp.zoom })
  }

  const g = gesture.current
  const byId = new Map(c.nodes.map((n) => [n.id, n]))
  const single = ed.sel.length === 1 ? byId.get(ed.sel[0]) : null
  const gridPx = ed.grid * vp.zoom
  const hs = 10 / vp.zoom

  return (
    <div
      ref={viewportRef}
      className={`board-viewport tool-${ed.tool}${dropHint ? ' drop-hint' : ''}${ed.canEdit ? '' : ' readonly'}`}
      style={{
        background: c.canvas.bg,
        ...(gridPx >= 6
          ? {
              backgroundImage: 'radial-gradient(circle, rgba(127,127,127,0.45) 1px, transparent 1.2px)',
              backgroundSize: `${gridPx}px ${gridPx}px`,
              backgroundPosition: `${vp.x}px ${vp.y}px`,
            }
          : {}),
      }}
      tabIndex={0}
      role="region"
      aria-roledescription="canvas"
      aria-label={`Board canvas, ${c.nodes.length} things on it`}
      aria-describedby="board-canvas-help"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={(e) => {
        if (!ed.canEdit) return
        const w = world(e)
        const hit = hitTest(c.nodes, w.x, w.y)
        if (!hit) return
        if (hit.type === 'item' && hit.item_id) setPreviewId(hit.item_id)
        else ed.setEditingText(hit.id)
      }}
      onDragOver={onDragOver}
      onDragLeave={() => setDropHint(false)}
      onDrop={(e) => void onDrop(e)}
      onKeyDown={onKeyDown}
    >
      <p id="board-canvas-help" className="sr-only">
        {ed.canEdit
          ? 'Tab moves from one thing to the next. Arrow keys move the selection, Delete removes it, Command D duplicates, Command G groups. The List button shows everything as a list.'
          : 'Drag or use the wheel to look around. The List button shows everything as a list.'}
      </p>
      <div className="board-world" style={{ transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})` }}>
        {order.map((n) => (
          <NodeView key={n.id} n={n} ed={ed} selected={ed.sel.includes(n.id)} />
        ))}
        <svg className="board-edges" aria-hidden="true">
          <defs>
            <marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse" markerUnits="userSpaceOnUse">
              <path d="M0,0 L10,5 L0,10 z" fill="context-stroke" />
            </marker>
          </defs>
          {c.edges.map((e) => {
            const a = byId.get(e.from)
            const b = byId.get(e.to)
            if (!a || !b) return null
            const l = edgeLine(rectOf(a), rectOf(b))
            const color = e.style?.color ?? '#57524a'
            const arrow = e.style?.arrow ?? 'end'
            const on = ed.edge === e.id
            return (
              <g key={e.id} className={`board-edge${on ? ' selected' : ''}`}>
                <line x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} className="edge-hit" data-edge={e.id} strokeWidth={14 / vp.zoom} />
                <line
                  x1={l.x1}
                  y1={l.y1}
                  x2={l.x2}
                  y2={l.y2}
                  stroke={on ? 'var(--accent)' : color}
                  strokeWidth={2 / Math.min(1, vp.zoom)}
                  strokeDasharray={e.style?.dash ? '8 6' : undefined}
                  markerEnd={arrow !== 'none' ? 'url(#arrowhead)' : undefined}
                  markerStart={arrow === 'both' ? 'url(#arrowhead)' : undefined}
                  pointerEvents="none"
                />
                {e.label && (
                  <text x={(l.x1 + l.x2) / 2} y={(l.y1 + l.y2) / 2 - 6} className="edge-label" textAnchor="middle" data-edge={e.id}>
                    {e.label}
                  </text>
                )}
              </g>
            )
          })}
          {g?.kind === 'connect' &&
            (() => {
              const a = byId.get(g.from)
              if (!a) return null
              return <line x1={a.x + a.w / 2} y1={a.y + a.h / 2} x2={g.wx} y2={g.wy} className="edge-draft" strokeWidth={2 / vp.zoom} />
            })()}
        </svg>
        {ed.canEdit &&
          single &&
          single.type !== 'group' &&
          HANDLES.map((h) => (
            <span
              key={h}
              className={`bhandle ${h}`}
              data-handle={h}
              data-node={single.id}
              style={{
                left: (h.includes('w') ? single.x : single.x + single.w) - hs / 2,
                top: (h.includes('n') ? single.y : single.y + single.h) - hs / 2,
                width: hs,
                height: hs,
                borderWidth: 1.5 / vp.zoom,
              }}
              aria-hidden="true"
            />
          ))}
        {g?.kind === 'marquee' && g.rect && (
          <span className="board-marquee" style={{ left: g.rect.x, top: g.rect.y, width: g.rect.w, height: g.rect.h, borderWidth: 1 / vp.zoom }} aria-hidden="true" />
        )}
      </div>
      {c.nodes.length === 0 && (
        <div className="board-empty-hint">
          <p>{ed.canEdit ? 'An empty board. Open Library to drag items in, or add a note (N), text (T) or a shape (R).' : 'This board is empty.'}</p>
        </div>
      )}
    </div>
  )
}

function NodeView({ n, ed, selected }: { n: BoardNode; ed: Editor; selected: boolean }) {
  const editing = ed.editingText === n.id
  const style: React.CSSProperties = {
    left: n.x,
    top: n.y,
    width: n.w,
    height: n.h,
    transform: n.rot ? `rotate(${n.rot}deg)` : undefined,
    zIndex: undefined,
  }
  const bw = (selected ? 2 : 1) / ed.vp.zoom
  const s = n.style ?? {}
  const textStyle: React.CSSProperties = { color: (s.color as string) ?? '#1c1b19', fontSize: (s.fontSize as number) ?? undefined }

  let body: React.ReactNode = null
  if (n.type === 'item') {
    const it = n.item_id ? ed.items.get(n.item_id) : undefined
    const gone = n.item_id ? ed.missing.has(n.item_id) : true
    body = it ? <ItemImage item={it} n={n} zoom={ed.vp.zoom} /> : (
      <span className={`bnode-missing${gone ? '' : ' loading'}`}>
        {gone ? (
          <>
            <ImageOff size={20} aria-hidden="true" /> Missing item
          </>
        ) : (
          ''
        )}
      </span>
    )
  } else if (n.type === 'color') {
    body = (
      <>
        <span className="swatch-fill" style={{ background: s.bg as string }} />
        <span className="swatch-hex">{(s.bg as string) ?? ''}</span>
      </>
    )
  } else if (n.type === 'link') {
    const url = (s.url as string) || ''
    body = (
      <span className="link-card">
        <Link2 size={16} aria-hidden="true" />
        <span>
          <span className="link-title">{n.text || url || 'Link'}</span>
          {url && <span className="link-url">{url}</span>}
        </span>
      </span>
    )
  } else if (n.type === 'group') {
    body = <span className="group-label">{n.text}</span>
  }
  const textual = n.type === 'note' || n.type === 'text' || n.type === 'shape'

  return (
    <div
      className={`bnode t-${n.type}${selected ? ' selected' : ''}${n.type === 'shape' ? ` shape-${s.shape ?? 'rect'}` : ''}${editing ? ' bnode-editing' : ''}`}
      style={{
        ...style,
        background: n.type === 'note' || n.type === 'shape' ? (s.bg as string) : n.type === 'group' ? `${(s.bg as string) ?? '#7f7f7f'}22` : undefined,
        outlineWidth: selected ? bw : undefined,
      }}
      data-node={n.id}
    >
      {body}
      {textual &&
        (editing ? (
          <textarea
            className="bnode-text-edit"
            style={textStyle}
            autoFocus
            aria-label={`Text of the ${n.type}`}
            defaultValue={n.text ?? ''}
            onBlur={(e) => {
              const text = e.target.value
              ed.setEditingText(null)
              if (text !== (n.text ?? '')) ed.commit({ ...ed.content, nodes: ed.content.nodes.map((x) => (x.id === n.id ? { ...x, text } : x)) })
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape' || (e.key === 'Enter' && (e.metaKey || e.ctrlKey))) {
                e.stopPropagation()
                ;(e.target as HTMLTextAreaElement).blur()
              }
            }}
          />
        ) : (
          <span className="bnode-text" style={textStyle}>
            {n.text}
          </span>
        ))}
    </div>
  )
}

function ItemImage({ item, n, zoom }: { item: Item; n: BoardNode; zoom: number }) {
  const shown = Math.max(n.w, n.h) * zoom * (window.devicePixelRatio || 1) / Math.min(n.crop?.w ?? 1, n.crop?.h ?? 1)
  const size = shown > 600 ? 1024 : shown > 280 ? 512 : 256
  const src = thumbUrl(item, size as 256 | 512 | 1024) ?? `/api/items/${item.id}/file`
  return (
    <span className="bnode-img">
      <img src={src} alt={item.filename} draggable={false} style={{ position: 'absolute', ...cropStyle(n.crop) }} loading="lazy" />
    </span>
  )
}
