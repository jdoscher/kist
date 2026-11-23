// The selection's properties: position and size as numbers (the precise,
// non-dragging way to move things), colours, text size, link URL, crop,
// order, align/distribute, group, and an arrow's label and style.

import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignStartHorizontal,
  AlignStartVertical,
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronDown,
  ChevronUp,
  Copy,
  Crop,
  ExternalLink,
  Group,
  Spline,
  Trash2,
  Ungroup,
  X,
} from 'lucide-react'
import { useId } from 'react'
import {
  align,
  croppedHeight,
  distribute,
  duplicate,
  group,
  moveNodes,
  newNodeId,
  nodeLabel,
  NOTE_COLOURS,
  removeNodes,
  reorder,
  SWATCHES,
  ungroup,
  type AlignMode,
  type BoardNode,
  type OrderMode,
} from '../../../board'
import { useAppState } from '../../../state'
import { boardWalkOrder, setPreviewWalk } from '../../../boardWalk'
import type { Editor } from './BoardEditor'
import { CropDialog } from './CropDialog'
import { BoardCutoutButton } from '../../cutouts/Cutouts' // F7

const ALIGN: { mode: AlignMode; label: string; Icon: typeof AlignStartVertical }[] = [
  { mode: 'left', label: 'Align left edges', Icon: AlignStartVertical },
  { mode: 'center', label: 'Align centres (across)', Icon: AlignCenterVertical },
  { mode: 'right', label: 'Align right edges', Icon: AlignEndVertical },
  { mode: 'top', label: 'Align tops', Icon: AlignStartHorizontal },
  { mode: 'middle', label: 'Align middles', Icon: AlignCenterHorizontal },
  { mode: 'bottom', label: 'Align bottoms', Icon: AlignEndHorizontal },
]

export function BoardInspector({ ed }: { ed: Editor }) {
  const { setPreviewId } = useAppState()
  const c = ed.content
  const uid = useId()
  const sel = c.nodes.filter((n) => ed.sel.includes(n.id))
  const one = sel.length === 1 ? sel[0] : null
  const edge = ed.edge ? c.edges.find((e) => e.id === ed.edge) : null

  const updateNode = (id: string, patch: Partial<BoardNode>) =>
    ed.commit({ ...c, nodes: c.nodes.map((n) => (n.id === id ? { ...n, ...patch, style: patch.style ? { ...n.style, ...patch.style } : n.style } : n)) })
  const order = (mode: OrderMode) => ed.commit({ ...c, nodes: reorder(c.nodes, ed.sel, mode) })
  const del = () => {
    ed.commit({ ...c, ...removeNodes(c, ed.sel) })
    ed.setSel([])
    ed.announce('Deleted')
  }
  const dup = () => {
    const out = duplicate(c, ed.sel, ed.grid || 24)
    ed.commit({ ...c, nodes: out.nodes, edges: out.edges })
    ed.setSel(out.newIds)
  }

  if (edge) {
    const from = c.nodes.find((n) => n.id === edge.from)
    const to = c.nodes.find((n) => n.id === edge.to)
    const name = (n?: BoardNode) => (n ? nodeLabel(n, (i) => ed.items.get(i)?.filename) : '?')
    const setEdge = (patch: Partial<typeof edge>) =>
      ed.commit({ ...c, edges: c.edges.map((e) => (e.id === edge.id ? { ...e, ...patch, style: patch.style ? { ...e.style, ...patch.style } : e.style } : e)) })
    return (
      <aside className="board-inspector" aria-label="Arrow">
        <div className="insp-head">
          <h2>Arrow</h2>
          <button className="icon-btn" aria-label="Deselect" onClick={() => ed.setEdge(null)}>
            <X size={14} aria-hidden="true" />
          </button>
        </div>
        <p className="hint">
          {name(from)} → {name(to)}
        </p>
        <label htmlFor={`${uid}-label`}>Label</label>
        <input id={`${uid}-label`} key={edge.id} defaultValue={edge.label ?? ''} onBlur={(e) => e.target.value !== (edge.label ?? '') && setEdge({ label: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
        <label htmlFor={`${uid}-arrow`}>Heads</label>
        <select id={`${uid}-arrow`} value={edge.style?.arrow ?? 'end'} onChange={(e) => setEdge({ style: { arrow: e.target.value as 'end' } })}>
          <option value="end">At the end</option>
          <option value="both">Both ends</option>
          <option value="none">None (a line)</option>
        </select>
        <label className="check-inline">
          <input type="checkbox" checked={!!edge.style?.dash} onChange={(e) => setEdge({ style: { dash: e.target.checked } })} /> Dashed
        </label>
        <button className="btn-icon" onClick={() => ed.commit({ ...c, edges: c.edges.map((e) => (e.id === edge.id ? { ...e, from: e.to, to: e.from } : e)) })}>
          Reverse
        </button>
        <button
          className="btn-icon danger"
          onClick={() => {
            ed.commit({ ...c, edges: c.edges.filter((e) => e.id !== edge.id) })
            ed.setEdge(null)
          }}
        >
          <Trash2 size={14} aria-hidden="true" /> Delete arrow
        </button>
      </aside>
    )
  }

  if (!sel.length) return null
  const num = (label: string, key: 'x' | 'y' | 'w' | 'h', n: BoardNode) => (
    <label className="insp-num">
      <span>{label}</span>
      <input
        type="number"
        key={`${n.id}-${key}-${Math.round(n[key])}`}
        defaultValue={Math.round(n[key])}
        min={key === 'w' || key === 'h' ? 16 : undefined}
        onBlur={(e) => {
          const v = Number(e.target.value)
          if (!Number.isFinite(v) || Math.round(v) === Math.round(n[key])) return
          if (key === 'x' || key === 'y') {
            const dx = key === 'x' ? v - n.x : 0
            const dy = key === 'y' ? v - n.y : 0
            // a group's children come along
            ed.commit({ ...c, nodes: moveNodes(c.nodes, [n.id], dx, dy) })
          } else updateNode(n.id, { [key]: Math.max(16, v) } as Partial<BoardNode>)
        }}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
    </label>
  )

  const cropNode = ed.cropFor ? c.nodes.find((n) => n.id === ed.cropFor) : null
  const cropItem = cropNode?.item_id ? ed.items.get(cropNode.item_id) : undefined

  return (
    <aside className="board-inspector" aria-label="Selection">
      <div className="insp-head">
        <h2>{one ? nodeLabel(one, (i) => ed.items.get(i)?.filename) : `${sel.length} selected`}</h2>
        <button className="icon-btn" aria-label="Deselect" onClick={() => ed.setSel([])}>
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      {one && (
        <>
          <div className="insp-grid" role="group" aria-label="Position and size">
            {num('X', 'x', one)}
            {num('Y', 'y', one)}
            {num('W', 'w', one)}
            {num('H', 'h', one)}
          </div>
          {(one.type === 'note' || one.type === 'shape' || one.type === 'group') && (
            <div className="insp-row" role="group" aria-label="Fill colour">
              <span className="insp-label">Colour</span>
              {[...NOTE_COLOURS, ...(one.type === 'note' ? [] : ['#ffffff', '#1c1b19'])].map((col) => (
                <button
                  key={col}
                  className={`swatch-btn${one.style?.bg === col ? ' on' : ''}`}
                  style={{ background: col }}
                  aria-label={`Fill ${col}`}
                  aria-pressed={one.style?.bg === col}
                  onClick={() => updateNode(one.id, { style: { bg: col, color: col === '#1c1b19' ? '#f5f5f4' : '#1c1b19' } })}
                />
              ))}
            </div>
          )}
          {one.type === 'shape' && (
            <label className="insp-field">
              Shape
              <select value={one.style?.shape ?? 'rect'} onChange={(e) => updateNode(one.id, { style: { shape: e.target.value as 'rect' } })}>
                <option value="rect">Rectangle</option>
                <option value="ellipse">Ellipse</option>
                <option value="diamond">Diamond</option>
              </select>
            </label>
          )}
          {(one.type === 'text' || one.type === 'note' || one.type === 'shape') && (
            <>
              <label className="insp-field">
                Text size
                <select value={one.style?.fontSize ?? (one.type === 'text' ? 28 : 15)} onChange={(e) => updateNode(one.id, { style: { fontSize: Number(e.target.value) } })}>
                  {[12, 15, 18, 22, 28, 36, 48, 64].map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
              <label className="insp-field" htmlFor={`${uid}-text`}>
                Text
              </label>
              <textarea id={`${uid}-text`} key={`${one.id}-${one.text}`} rows={3} defaultValue={one.text ?? ''} onBlur={(e) => e.target.value !== (one.text ?? '') && updateNode(one.id, { text: e.target.value })} />
            </>
          )}
          {one.type === 'group' && (
            <label className="insp-field">
              Name
              <input key={one.id} defaultValue={one.text ?? ''} onBlur={(e) => e.target.value !== (one.text ?? '') && updateNode(one.id, { text: e.target.value })} />
            </label>
          )}
          {one.type === 'color' && (
            <>
              <div className="insp-row" role="group" aria-label="Swatch colour">
                {SWATCHES.map((col) => (
                  <button key={col} className={`swatch-btn${one.style?.bg === col ? ' on' : ''}`} style={{ background: col }} aria-label={`Colour ${col}`} aria-pressed={one.style?.bg === col} onClick={() => updateNode(one.id, { style: { bg: col } })} />
                ))}
              </div>
              <label className="insp-field">
                Hex
                <input
                  key={`${one.id}-${one.style?.bg}`}
                  defaultValue={(one.style?.bg as string) ?? ''}
                  className="mono"
                  onBlur={(e) => /^#[0-9a-f]{6}$/i.test(e.target.value) && updateNode(one.id, { style: { bg: e.target.value.toLowerCase() } })}
                />
              </label>
            </>
          )}
          {one.type === 'link' && (
            <>
              <label className="insp-field">
                URL
                <input key={`${one.id}-u`} type="url" defaultValue={(one.style?.url as string) ?? ''} placeholder="https://…" onBlur={(e) => updateNode(one.id, { style: { url: e.target.value.trim() } })} />
              </label>
              <label className="insp-field">
                Title
                <input key={`${one.id}-t`} defaultValue={one.text ?? ''} onBlur={(e) => updateNode(one.id, { text: e.target.value })} />
              </label>
              {/^https?:\/\//.test((one.style?.url as string) ?? '') && (
                <a href={one.style?.url as string} target="_blank" rel="noreferrer noopener" className="btn-link">
                  Open link <ExternalLink size={14} aria-hidden="true" />
                  <span className="sr-only"> (new tab)</span>
                </a>
              )}
            </>
          )}
          {one.type === 'item' && one.item_id && (
            <div className="insp-row">
              <button className="btn-icon" onClick={() => ed.setCropFor(one.id)} disabled={!ed.items.get(one.item_id)}>
                <Crop size={14} aria-hidden="true" /> Crop…
              </button>
              {one.crop && (
                <button
                  onClick={() => {
                    const it = ed.items.get(one.item_id as string)
                    updateNode(one.id, { crop: null, h: it?.width && it.height ? (one.w * it.height) / it.width : one.h })
                  }}
                >
                  Reset crop
                </button>
              )}
              <button
                onClick={() => {
                  setPreviewWalk(boardWalkOrder(ed.content.nodes).filter((id) => !ed.missing.has(id)))
                  setPreviewId(one.item_id as string)
                }}
              >
                Open item
              </button>
              <BoardCutoutButton boardId={ed.id} nodeId={one.id} item={ed.items.get(one.item_id)} />
            </div>
          )}
        </>
      )}

      {sel.length >= 2 && (
        <>
          <div className="insp-row" role="group" aria-label="Align">
            {ALIGN.map(({ mode, label, Icon }) => (
              <button key={mode} className="icon-btn" aria-label={label} title={label} onClick={() => ed.commit({ ...c, nodes: align(c.nodes, ed.sel, mode) })}>
                <Icon size={16} aria-hidden="true" />
              </button>
            ))}
          </div>
          <div className="insp-row" role="group" aria-label="Distribute">
            <button disabled={sel.length < 3} onClick={() => ed.commit({ ...c, nodes: distribute(c.nodes, ed.sel, 'h') })}>
              Space across
            </button>
            <button disabled={sel.length < 3} onClick={() => ed.commit({ ...c, nodes: distribute(c.nodes, ed.sel, 'v') })}>
              Space down
            </button>
          </div>
        </>
      )}
      {sel.length === 2 && (
        <button
          className="btn-icon"
          onClick={() => {
            ed.commit({ ...c, edges: [...c.edges, { id: newNodeId(), from: sel[0].id, to: sel[1].id, label: '', style: { arrow: 'end' } }] })
            ed.announce('Arrow added')
          }}
        >
          <Spline size={14} aria-hidden="true" /> Connect with an arrow
        </button>
      )}

      <div className="insp-row" role="group" aria-label="Order">
        <button className="icon-btn" aria-label="Bring to front" title="Bring to front" onClick={() => order('front')}>
          <ArrowUpToLine size={16} aria-hidden="true" />
        </button>
        <button className="icon-btn" aria-label="Bring forward" title="Bring forward" onClick={() => order('forward')}>
          <ChevronUp size={16} aria-hidden="true" />
        </button>
        <button className="icon-btn" aria-label="Send backward" title="Send backward" onClick={() => order('backward')}>
          <ChevronDown size={16} aria-hidden="true" />
        </button>
        <button className="icon-btn" aria-label="Send to back" title="Send to back" onClick={() => order('back')}>
          <ArrowDownToLine size={16} aria-hidden="true" />
        </button>
      </div>
      <div className="insp-row">
        <button className="btn-icon" onClick={dup} title="⌘D">
          <Copy size={14} aria-hidden="true" /> Duplicate
        </button>
        {sel.some((n) => n.type === 'group') ? (
          <button
            className="btn-icon"
            onClick={() => {
              ed.commit({ ...c, nodes: ungroup(c.nodes, ed.sel) })
              ed.setSel([])
            }}
            title="⇧⌘G"
          >
            <Ungroup size={14} aria-hidden="true" /> Ungroup
          </button>
        ) : (
          <button
            className="btn-icon"
            onClick={() => {
              const g = group(c.nodes, ed.sel)
              if (g) {
                ed.commit({ ...c, nodes: g.nodes })
                ed.setSel([g.id])
              }
            }}
            title="⌘G"
          >
            <Group size={14} aria-hidden="true" /> Group
          </button>
        )}
        <button className="btn-icon danger" onClick={del} title="Delete">
          <Trash2 size={14} aria-hidden="true" /> Delete
        </button>
      </div>

      {cropNode && cropItem && (
        <CropDialog
          item={cropItem}
          crop={cropNode.crop ?? null}
          onClose={() => ed.setCropFor(null)}
          onApply={(crop) => {
            const h = croppedHeight(cropNode.w, cropItem.width ?? 0, cropItem.height ?? 0, crop)
            updateNode(cropNode.id, { crop, h })
            ed.setCropFor(null)
          }}
        />
      )}
    </aside>
  )
}
