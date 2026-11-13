import { ExternalLink, Lock, Maximize2, Star, X } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { emptyQuery, fileUrl, thumbUrl, type Item } from '../api'
import { useCaps, useCurrentItems, useFolderNames, useItem } from '../data'
import { swipeDirection } from '../mobile'
import { useAppState } from '../state'
import { ItemHistory } from './ItemHistory'
import { ItemLinks } from './ItemLinks'
import { ItemMeta } from './ItemMeta'
import { ItemSections } from './m45/lazy'
import { SheetVersions } from './m3/ItemCreative'
import { ItemStatus } from './ItemStatus'
import { PhoneItemEdit, PhoneStars } from './PhoneEdit'
import { ItemKinds } from './kinds/KindsUI'
import { CaptionField, ImpliedTags, ItemFaces, MatchWhy, MoreLikeThisButton, PaletteSwatches, StackInfo, TagText } from './ItemAI'

/** An item's details without edit controls: the phone sheet's body, and
 * the info panel for a read-only token. Tags and folders are links to
 * their views (left out when the phone sheet shows them editable). */
export function ReadOnlyItem({
  item,
  withThumb = false,
  afterStack,
  editing = false,
}: {
  item: Item
  withThumb?: boolean
  afterStack?: ReactNode
  /** the phone's edit controls show tags and folders instead */
  editing?: boolean
}) {
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
              Open <Maximize2 size={14} aria-hidden="true" />
            </button>
          </div>
          <h2 className="detail-filename readonly">{item.filename}</h2>
        </>
      )}
      <ItemStatus item={item} />
      <MatchWhy item={item} />
      <MoreLikeThisButton item={item} />
      <CaptionField item={item} editable={false} />
      {item.protected && (
        <p className="protected-note">
          <Lock size={14} aria-hidden="true" /> Protected: it can’t be trashed or deleted.
        </p>
      )}
      {editing && <ImpliedTags item={item} />}
      {!editing && item.tags.length > 0 && (
        <div className="detail-section">
          <h3 className="section-label">Tags</h3>
          <div className="tag-chips">
            {item.tags.map((t) => (
              <button key={t} className="chip" onClick={() => setQuery({ ...emptyQuery, tags: [t], order: query.order })}>
                <TagText tag={t} />
              </button>
            ))}
          </div>
          <ImpliedTags item={item} />
        </div>
      )}
      {!editing && item.folder_ids.length > 0 && (
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
      <PaletteSwatches item={item} />
      <ItemFaces item={item} editable={false} />
      <StackInfo item={item} editable={false} />
      {afterStack}
      <ItemLinks item={item} editable={false} />
      <ItemMeta item={item} />
      <ItemSections item={item} />
      <a className="open-original" href={fileUrl(item)} target="_blank" rel="noreferrer">
        Open original <ExternalLink size={14} aria-hidden="true" />
        <span className="sr-only"> (new tab)</span>
      </a>
      <ItemHistory item={item} />
    </>
  )
}

/**
 * Phone layout: a sheet that slides up over the grid when one item is
 * tapped. Tap the picture (or View) for the full-screen preview; swipe the
 * handle down or tap ✕ to close. A token that may edit gets stars, tags,
 * Move to… and Trash here (PhoneEdit.tsx); everything else is read-only.
 */
export function DetailSheet() {
  const { selection, clearSelection, setPreviewId } = useAppState()
  const { items } = useCurrentItems()
  const start = useRef<{ x: number; y: number } | null>(null)
  const [drag, setDrag] = useState(0)
  const { canEdit } = useCaps()

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
          {canEdit ? (
            <PhoneStars item={item} />
          ) : item.rating > 0 && (
            <div className="sheet-stars" role="img" aria-label={`${item.rating} of 5 stars`}>
              {Array.from({ length: item.rating }, (_, i) => (
                <Star key={i} size={14} fill="currentColor" strokeWidth={0} aria-hidden="true" />
              ))}
            </div>
          )}
          <button className="btn-primary" onClick={() => setPreviewId(item.id)}>
            View
          </button>
        </div>
        <button className="sheet-close" onClick={clearSelection} aria-label="Close item details">
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="sheet-body">
        {canEdit && <PhoneItemEdit key={item.id} item={item} />}
        <ItemKinds item={item} />
        <ReadOnlyItem item={item} editing={canEdit} afterStack={<SheetVersions item={item} />} />
      </div>
    </aside>
  )
}
