// Everything on the board as a list: the screen-reader and keyboard view
// (WCAG 2.5.7: every drag on the canvas has a non-dragging way here, with
// the inspector beside it for position, size, colour and text).

import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { useId, useState } from 'react'
import { byZ, newNodeId, nodeLabel, removeNodes, reorder, type BoardNode } from '../../../board'
import type { Editor } from './BoardEditor'

export function BoardList({ ed }: { ed: Editor }) {
  const c = ed.content
  const uid = useId()
  const order = byZ(c.nodes).reverse()
  const name = (n?: BoardNode) => (n ? nodeLabel(n, (i) => ed.items.get(i)?.filename) : '?')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [label, setLabel] = useState('')
  const byId = new Map(c.nodes.map((n) => [n.id, n]))

  return (
    <div className="board-list" tabIndex={-1}>
      <h2 className="sr-only">Board contents</h2>
      {ed.canEdit && (
        <div className="insp-row" role="group" aria-label="Add">
          <button className="btn-icon" onClick={() => ed.add('note')}>
            <Plus size={14} aria-hidden="true" /> Note
          </button>
          <button className="btn-icon" onClick={() => ed.add('text')}>
            <Plus size={14} aria-hidden="true" /> Text
          </button>
          <button className="btn-icon" onClick={() => ed.add('shape')}>
            <Plus size={14} aria-hidden="true" /> Shape
          </button>
          <button className="btn-icon" onClick={() => ed.add('color')}>
            <Plus size={14} aria-hidden="true" /> Colour swatch
          </button>
          <button className="btn-icon" onClick={() => ed.add('link')}>
            <Plus size={14} aria-hidden="true" /> Link
          </button>
        </div>
      )}
      <table className="board-table">
        <caption>
          {c.nodes.length} thing{c.nodes.length === 1 ? '' : 's'} on the board, top first. Select one to edit its position, size, colour and text in
          the panel beside.
        </caption>
        <thead>
          <tr>
            <th scope="col">What</th>
            <th scope="col">Position</th>
            <th scope="col">Size</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {order.map((n, i) => {
            const selected = ed.sel.includes(n.id)
            return (
              <tr key={n.id} className={selected ? 'selected' : ''}>
                <th scope="row">
                  <button className="link-like" aria-pressed={selected} onClick={() => ed.setSel(selected ? ed.sel.filter((x) => x !== n.id) : [...ed.sel, n.id])}>
                    {name(n)}
                  </button>
                  {n.type === 'group' && n.children?.length ? <span className="hint"> · {n.children.map((id) => name(byId.get(id))).join(', ')}</span> : null}
                </th>
                <td className="mono">
                  {Math.round(n.x)}, {Math.round(n.y)}
                </td>
                <td className="mono">
                  {Math.round(n.w)} × {Math.round(n.h)}
                </td>
                <td className="row-btns">
                  {ed.canEdit && (
                    <>
                      <button className="icon-btn" aria-label={`Bring ${name(n)} forward`} title="Forward" disabled={i === 0} onClick={() => ed.commit({ ...c, nodes: reorder(c.nodes, [n.id], 'forward') })}>
                        <ArrowUp size={14} aria-hidden="true" />
                      </button>
                      <button className="icon-btn" aria-label={`Send ${name(n)} backward`} title="Backward" disabled={i === order.length - 1} onClick={() => ed.commit({ ...c, nodes: reorder(c.nodes, [n.id], 'backward') })}>
                        <ArrowDown size={14} aria-hidden="true" />
                      </button>
                      <button
                        className="icon-btn"
                        aria-label={`Delete ${name(n)}`}
                        title="Delete"
                        onClick={() => {
                          ed.commit({ ...c, ...removeNodes(c, [n.id]) })
                          ed.setSel(ed.sel.filter((x) => x !== n.id))
                          ed.announce(`Deleted ${name(n)}`)
                        }}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <h3 className="section-label">Arrows ({c.edges.length})</h3>
      <ul className="edge-list">
        {c.edges.map((e) => (
          <li key={e.id} className={ed.edge === e.id ? 'selected' : ''}>
            <button className="link-like" aria-pressed={ed.edge === e.id} onClick={() => ed.setEdge(ed.edge === e.id ? null : e.id)}>
              {name(byId.get(e.from))} → {name(byId.get(e.to))}
              {e.label ? `: “${e.label}”` : ''}
            </button>
            {ed.canEdit && (
              <button className="icon-btn" aria-label="Delete this arrow" onClick={() => ed.commit({ ...c, edges: c.edges.filter((x) => x.id !== e.id) })}>
                <Trash2 size={14} aria-hidden="true" />
              </button>
            )}
          </li>
        ))}
      </ul>
      {ed.canEdit && c.nodes.length >= 2 && (
        <form
          className="connect-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (!from || !to || from === to) return
            ed.commit({ ...c, edges: [...c.edges, { id: newNodeId(), from, to, label, style: { arrow: 'end' } }] })
            setLabel('')
            ed.announce('Arrow added')
          }}
        >
          <fieldset>
            <legend>Add an arrow</legend>
            <label htmlFor={`${uid}-from`}>From</label>
            <select id={`${uid}-from`} value={from} onChange={(e) => setFrom(e.target.value)}>
              <option value="">Choose…</option>
              {order.map((n) => (
                <option key={n.id} value={n.id}>
                  {name(n)}
                </option>
              ))}
            </select>
            <label htmlFor={`${uid}-to`}>To</label>
            <select id={`${uid}-to`} value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">Choose…</option>
              {order.map((n) => (
                <option key={n.id} value={n.id}>
                  {name(n)}
                </option>
              ))}
            </select>
            <label htmlFor={`${uid}-label`}>Label (optional)</label>
            <input id={`${uid}-label`} value={label} onChange={(e) => setLabel(e.target.value)} />
            <button type="submit" disabled={!from || !to || from === to}>
              Add arrow
            </button>
          </fieldset>
        </form>
      )}
    </div>
  )
}
