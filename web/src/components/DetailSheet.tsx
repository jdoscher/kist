import { ExternalLink, Lock, Maximize2, Star, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { emptyQuery, fileUrl, thumbUrl, type Item } from '../api'
import { useCurrentItems, useFolderNames, useItem } from '../data'
import { swipeDirection } from '../mobile'
import { useAppState } from '../state'
import { ItemHistory } from './ItemHistory'
import { ItemLinks } from './ItemLinks'
import { ItemMeta } from './ItemMeta'
import { ItemStatus } from './ItemStatus'

/** An item's details without edit controls: the phone sheet's body, and
 * the info panel for a read-only token. Tags and folders are links to
 * their views. */
export function ReadOnlyItem({ item, withThumb = false }: { item: Item; withThumb?: boolean }) {
  const { query, setQuery, setPreviewId } = useAppState()
  const folderName = useFolderNames()
  const thumb = thumbUrl(item, 512)
  return (
    <>
      {withThumb && (
        <>
          <div className="detail-thumb">
            {thumb ? <img src={thumb} alt="" /> : <div className="detail-thumb-icon" aria-hidden="true">.{item.ext}</div>}
            <button className="open-full" onClick={() => setPreviewId(item.id)} title="Open full screen">
              Open <Maximize2 size={13} strokeWidth={1.75} aria-hidden="true" />
            </button>
          </div>
          <h2 className="detail-filename readonly">{item.filename}</h2>
        </>
      )}
      <ItemStatus item={item} />
      {item.protected && (
        <p className="protected-note">
          <Lock size={13} strokeWidth={1.75} aria-hidden="true" /> Protected: it can’t be trashed or deleted.
        </p>
      )}
      {item.tags.length > 0 && (
        <div className="detail-section">
          <h3 className="section-label">Tags</h3>
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
          <h3 className="section-label">Folders</h3>
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
          <h3 className="section-label">Notes</h3>
          <p className="sheet-notes">{item.notes}</p>
        </div>
      )}
      <ItemLinks item={item} editable={false} />
      <ItemMeta item={item} />
      <a className="open-original" href={fileUrl(item)} target="_blank" rel="noreferrer">
        Open original <ExternalLink size={13} strokeWidth={1.75} aria-hidden="true" />
        <span className="sr-only"> (new tab)</span>
      </a>
      <ItemHistory item={item} />
    </>
  )
}

/**
 * Phone layout: a read-only sheet that slides up over the grid when one item
 * is tapped. Tap the picture (or View) for the full-screen preview; swipe the
 * handle down or tap ✕ to close.
 */
export function DetailSheet() {
  const { selection, clearSelection, setPreviewId } = useAppState()
  const { items } = useCurrentItems()
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
      aria-label="Item details"
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
          <img src={thumb} alt="" onClick={() => setPreviewId(item.id)} />
        ) : (
          <div className="sheet-icon" aria-hidden="true" onClick={() => setPreviewId(item.id)}>
            .{item.ext}
          </div>
        )}
        <div className="sheet-title">
          <h2 className="fname">{item.filename}</h2>
          {item.rating > 0 && (
            <div className="sheet-stars" role="img" aria-label={`${item.rating} of 5 stars`}>
              {Array.from({ length: item.rating }, (_, i) => (
                <Star key={i} size={14} fill="currentColor" strokeWidth={0} aria-hidden="true" />
              ))}
            </div>
          )}
          <button className="primary" onClick={() => setPreviewId(item.id)}>
            View
          </button>
        </div>
        <button className="sheet-close" onClick={clearSelection} aria-label="Close item details">
          <X size={16} strokeWidth={1.75} aria-hidden="true" />
        </button>
      </div>

      <div className="sheet-body">
        <ReadOnlyItem item={item} />
      </div>
    </aside>
  )
}
