// The library, slid out beside the board: search, then drag items onto the
// canvas, or press Add (keyboard, and the 2.5.7 alternative to dragging).

import { useQuery } from '@tanstack/react-query'
import { Plus, Search, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, thumbUrl, type Item } from '../../../api'
import { freeSpot, itemNodes, topZ } from '../../../board'
import { api3 } from '../../../creativeApi'
import { useAppState } from '../../../state'
import type { Editor } from './BoardEditor'

export function BoardLibrary({ ed, onClose }: { ed: Editor; onClose: () => void }) {
  const { selection } = useAppState()
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 200)
    return () => clearTimeout(t)
  }, [q])
  const results = useQuery({ queryKey: ['boardLibrary', debounced], queryFn: () => api.searchItems(debounced, 60), staleTime: 30_000, placeholderData: (p) => p })
  const items = results.data?.items ?? []
  const onBoard = new Set(ed.content.nodes.filter((n) => n.type === 'item').map((n) => n.item_id))
  const picked = [...selection]

  const place = (list: Item[]) => {
    if (!list.length) return
    ed.addItems(list)
    const { w, h } = ed.viewSize()
    const c = ed.content
    const { x, y } = freeSpot(c.nodes, (w / 2 - ed.vp.x) / ed.vp.zoom - 120, (h / 2 - ed.vp.y) / ed.vp.zoom - 120)
    const nodes = itemNodes(list, x, y, topZ(c.nodes), ed.grid)
    ed.commit({ ...c, nodes: [...c.nodes, ...nodes] })
    ed.setSel(nodes.map((n) => n.id))
    ed.announce(`Added ${list.length} to the board`)
  }

  return (
    <aside className="board-library" aria-label="Library">
      <div className="insp-head">
        <h2>Library</h2>
        <button className="icon-btn" aria-label="Close the library" onClick={onClose}>
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <div className="lib-search">
        <Search size={14} aria-hidden="true" />
        <input type="search" aria-label="Search the library" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {picked.length > 0 && (
        <button
          className="btn-icon"
          onClick={() => void api3.itemsByIds(picked).then((r) => place(r.items))}
          title="The items you had selected in the library"
        >
          <Plus size={14} aria-hidden="true" /> Add the {picked.length} selected
        </button>
      )}
      <p className="hint">Drag onto the board, or press +.</p>
      <ul className="lib-grid">
        {items.map((it) => {
          const thumb = thumbUrl(it, 256)
          return (
            <li
              key={it.id}
              className={`lib-cell${onBoard.has(it.id) ? ' on-board' : ''}`}
              draggable
              onDragStart={(e) => {
                ed.addItems([it])
                e.dataTransfer.setData('application/x-kist-items', JSON.stringify([it.id]))
                e.dataTransfer.effectAllowed = 'copy'
              }}
              title={it.filename}
            >
              {thumb ? <img src={thumb} alt="" loading="lazy" draggable={false} /> : <span className="lib-ext">.{it.ext}</span>}
              <button className="lib-add icon-btn" aria-label={`Add ${it.filename} to the board${onBoard.has(it.id) ? ' (already on it)' : ''}`} onClick={() => place([it])}>
                <Plus size={14} aria-hidden="true" />
              </button>
            </li>
          )
        })}
      </ul>
      {results.isSuccess && !items.length && <p className="hint">Nothing matches.</p>}
    </aside>
  )
}
