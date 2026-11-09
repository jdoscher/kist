import { useRef, useState } from 'react'
import { emptyQuery, fileUrl, thumbUrl } from '../api'
import { useCurrentItems, useFolderNames, useItem } from '../data'
import { swipeDirection } from '../mobile'
import { useAppState } from '../state'
import { ItemMeta } from './ItemMeta'
import { ItemStatus } from './ItemStatus'

/**
 * Phone layout: a read-only sheet that slides up over the grid when one item
 * is tapped. Tap the picture (or View) for the full-screen preview; swipe the
 * handle down or tap ✕ to close.
 */
export function DetailSheet() {
  const { query, setQuery, selection, clearSelection, setPreviewId } = useAppState()
  const { items } = useCurrentItems()
  const folderName = useFolderNames()
  const start = useRef<{ x: number; y: number } | null>(null)
  const [drag, setDrag] = useState(0)

  const item = useItem(selection.size === 1 ? [...selection][0] : null, items)
  if (!item) return null
  const thumb = thumbUrl(item, 512)

  const onTouchStart = (e: React.TouchEvent) => {
    start.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
  }
  const onTouchMove = (e: React.TouchEvent) => {
    if (start.current) setDrag(Math.max(0, e.touches[0].clientY - start.current.y))
  }
  const onTouchEnd = (e: React.TouchEvent) => {
    const s = start.current
    start.current = null
    setDrag(0)
    if (!s) return
    const t = e.changedTouches[0]
    if (swipeDirection(t.clientX - s.x, t.clientY - s.y) === 'down') clearSelection()
  }

  return (
    <aside
      className="detail-sheet"
      style={drag ? { transform: `translateY(${drag}px)`, transition: 'none' } : undefined}
    >
      <div
        className="sheet-grab"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        <span />
      </div>
      <div className="sheet-head">
        {thumb ? (
          <img src={thumb} alt={item.filename} onClick={() => setPreviewId(item.id)} />
        ) : (
          <div className="sheet-icon" onClick={() => setPreviewId(item.id)}>
            .{item.ext}
          </div>
        )}
        <div className="sheet-title">
          <div className="fname">{item.filename}</div>
          {item.rating > 0 && <div className="sheet-stars">{'★'.repeat(item.rating)}</div>}
          <button className="primary" onClick={() => setPreviewId(item.id)}>
            View
          </button>
        </div>
        <button className="sheet-close" onClick={clearSelection} aria-label="Close">
          ✕
        </button>
      </div>

      <div className="sheet-body">
        <ItemStatus item={item} />
        {item.tags.length > 0 && (
          <div className="detail-section">
            <label>Tags</label>
            <div className="tag-chips">
              {item.tags.map((t) => (
                <button key={t} className="chip" onClick={() => setQuery({ ...emptyQuery, tags: [t], order: query.order })}>
                  #{t}
                </button>
              ))}
            </div>
          </div>
        )}
        {item.folder_ids.length > 0 && (
          <div className="detail-section">
            <label>Folders</label>
            <div className="tag-chips">
              {item.folder_ids.map((fid) => (
                <button
                  key={fid}
                  className="chip folder-chip"
                  onClick={() => setQuery({ ...emptyQuery, folder: fid, order: query.order })}
                >
                  {folderName.get(fid) ?? fid}
                </button>
              ))}
            </div>
          </div>
        )}
        {item.notes && (
          <div className="detail-section">
            <label>Notes</label>
            <p className="sheet-notes">{item.notes}</p>
          </div>
        )}
        <ItemMeta item={item} />
        <a className="open-original" href={fileUrl(item)} target="_blank" rel="noreferrer">
          Open original ↗
        </a>
      </div>
    </aside>
  )
}
