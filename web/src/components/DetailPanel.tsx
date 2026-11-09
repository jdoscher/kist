import { useEffect, useMemo, useRef, useState } from 'react'
import { api, emptyQuery, fileUrl, thumbUrl, type Item } from '../api'
import { swipeDirection } from '../mobile'
import {
  flattenItems,
  useAppState,
  useFolders,
  useInvalidateItems,
  useItem,
  useItems,
  useStats,
} from '../state'

function fmtBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(2)} GB`
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(2)} MB`
  if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(1)} KB`
  return `${n} B`
}

function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="stars">
      {[1, 2, 3, 4, 5].map((s) => (
        <button
          key={s}
          className={s <= value ? 'on' : ''}
          onClick={() => onChange(s === value ? 0 : s)}
        >
          ★
        </button>
      ))}
    </div>
  )
}

/** The item's server-side states, with the action that moves each one on:
 * Trash -> Restore, failed preview -> Retry. Shown on desktop and phone. */
export function ItemStatus({ item }: { item: Item }) {
  const invalidate = useInvalidateItems()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      invalidate()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setBusy(false)
    }
  }
  if (item.state === 'active' && (item.preview_status === 'ready' || item.preview_status === 'none'))
    return null
  return (
    <div className="item-status">
      {item.state === 'trashed' && (
        <div className="status-row trashed">
          <span>In Trash</span>
          <button disabled={busy} onClick={() => run(() => api.restoreItem(item.id))}>
            Restore
          </button>
        </div>
      )}
      {item.preview_status === 'failed' && (
        <div className="status-row failed">
          <span title={item.preview_error ?? ''}>
            Preview failed{item.preview_error ? `: ${item.preview_error}` : ''}
          </span>
          <button disabled={busy} onClick={() => run(() => api.retryPreview(item.id))}>
            Retry
          </button>
        </div>
      )}
      {item.preview_status === 'pending' && (
        <div className="status-row pending">
          <span>Making the preview…</span>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  )
}

function SingleItem({ item }: { item: Item }) {
  const invalidate = useInvalidateItems()
  const { data: folders } = useFolders()
  const folderName = useMemo(
    () => new Map((folders ?? []).map((f) => [f.id, f.name])),
    [folders],
  )
  const [filename, setFilename] = useState(item.filename)
  const [notes, setNotes] = useState(item.notes)
  const [tagText, setTagText] = useState('')

  useEffect(() => {
    setFilename(item.filename)
    setNotes(item.notes)
  }, [item.id, item.filename, item.notes])

  const save = async (patch: Parameters<typeof api.patchItem>[1]) => {
    await api.patchItem(item.id, patch)
    invalidate()
  }

  const addTags = async () => {
    const extra = tagText.split(',').map((t) => t.trim()).filter(Boolean)
    if (!extra.length) return
    await api.putTags(item.id, [...new Set([...item.tags, ...extra])])
    setTagText('')
    invalidate()
  }

  const removeTag = async (tag: string) => {
    await api.putTags(item.id, item.tags.filter((t) => t !== tag))
    invalidate()
  }

  return (
    <>
      <ItemStatus item={item} />
      <input
        className="detail-filename"
        value={filename}
        onChange={(e) => setFilename(e.target.value)}
        onBlur={() => filename.trim() && filename !== item.filename && save({ filename: filename.trim() })}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />

      <Stars value={item.rating} onChange={(rating) => save({ rating })} />

      <div className="detail-section">
        <label>Tags</label>
        <div className="tag-chips">
          {item.tags.map((t) => (
            <span key={t} className="chip">
              #{t}
              <button onClick={() => removeTag(t)}>×</button>
            </span>
          ))}
        </div>
        <input
          placeholder="Add tags, comma-sep, Enter"
          value={tagText}
          onChange={(e) => setTagText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && addTags()}
        />
      </div>

      <div className="detail-section">
        <label>Folders</label>
        <div className="tag-chips">
          {item.folder_ids.map((fid) => (
            <span key={fid} className="chip folder-chip">
              {folderName.get(fid) ?? fid}
              <button
                onClick={async () => {
                  await api.putFolders(item.id, item.folder_ids.filter((f) => f !== fid))
                  invalidate()
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <select
          value=""
          onChange={async (e) => {
            if (e.target.value && !item.folder_ids.includes(e.target.value)) {
              await api.putFolders(item.id, [...item.folder_ids, e.target.value])
              invalidate()
            }
          }}
        >
          <option value="">Add to folder…</option>
          {(folders ?? [])
            .filter((f) => !item.folder_ids.includes(f.id))
            .map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
        </select>
      </div>

      <div className="detail-section">
        <label>Notes</label>
        <textarea
          rows={4}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => notes !== item.notes && save({ notes })}
          placeholder="Notes…"
        />
      </div>

      <ItemMeta item={item} />

      <a className="open-original" href={fileUrl(item)} target="_blank" rel="noreferrer">
        Open original ↗
      </a>
    </>
  )
}

function ItemMeta({ item }: { item: Item }) {
  return (
    <>
      <div className="detail-section meta-list">
        <div><span>Type</span><span>{item.mime}</span></div>
        <div><span>Size</span><span>{fmtBytes(item.size_bytes)}</span></div>
        {item.width != null && (
          <div><span>Dimensions</span><span>{item.width}×{item.height}</span></div>
        )}
        {item.duration_s != null && (
          <div><span>Duration</span><span>{item.duration_s.toFixed(1)}s</span></div>
        )}
        <div><span>Added</span><span>{new Date(item.created_at).toLocaleString()}</span></div>
        {item.source_url && (
          <div>
            <span>Source</span>
            <a href={item.source_url} target="_blank" rel="noreferrer">
              {new URL(item.source_url).hostname} ↗
            </a>
          </div>
        )}
      </div>

      {item.dominant_colors && item.dominant_colors.length > 0 && (
        <div className="color-strip">
          {item.dominant_colors.map((c) => (
            <span key={c.hex} style={{ background: c.hex, flexGrow: c.pct }} title={c.hex} />
          ))}
        </div>
      )}
    </>
  )
}

function LibrarySummary() {
  const { data: stats } = useStats()
  return (
    <div className="multi-summary">
      <h3>No selection</h3>
      <p className="hint-text">Click an item to see and edit its details.</p>
      {stats && (
        <div className="detail-section">
          <label>Library</label>
          <div className="meta-list">
            <div>
              <span>Items</span>
              <span>{stats.total_items.toLocaleString()}</span>
            </div>
            {Object.entries(stats.by_kind)
              .sort((a, b) => b[1] - a[1])
              .map(([kind, n]) => (
                <div key={kind}>
                  <span>{kind}</span>
                  <span>{n.toLocaleString()}</span>
                </div>
              ))}
            <div>
              <span>Size</span>
              <span>{fmtBytes(stats.total_size_bytes)}</span>
            </div>
            <div>
              <span>Tags</span>
              <span>{stats.tag_count.toLocaleString()}</span>
            </div>
            <div>
              <span>In trash</span>
              <span>{stats.trashed_items.toLocaleString()}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// always mounted while open (even with nothing selected) so selecting an
// item never changes the grid's width and reflows the columns
export function DetailPanel() {
  const { query, selection, panelOpen } = useAppState()
  const result = useItems(query)
  const items = useMemo(() => flattenItems(result.data?.pages), [result.data])
  const selected = useMemo(() => items.filter((i) => selection.has(i.id)), [items, selection])
  // one item that isn't in the loaded grid (opened from a link): fetch it
  const onlyId = selection.size === 1 ? [...selection][0] : null
  const single = useItem(onlyId, items)

  if (!panelOpen) return null

  return (
    <aside className="detail-panel">
      {selection.size === 0 ? (
        <LibrarySummary />
      ) : selection.size === 1 ? (
        single ? <SingleItem item={single} /> : <div className="hint">Loading…</div>
      ) : (
        <div className="multi-summary">
          <h3>{selected.length} items selected</h3>
          <p>{fmtBytes(selected.reduce((acc, i) => acc + i.size_bytes, 0))} total</p>
          <p className="hint">Use the bar below for bulk tag / move / trash.</p>
        </div>
      )}
    </aside>
  )
}

/**
 * Phone layout: a read-only sheet that slides up over the grid when one item
 * is tapped. Tap the picture (or View) for the full-screen preview; swipe the
 * handle down or tap ✕ to close.
 */
export function DetailSheet() {
  const { query, setQuery, selection, clearSelection, setPreviewId } = useAppState()
  const result = useItems(query)
  const items = useMemo(() => flattenItems(result.data?.pages), [result.data])
  const { data: folders } = useFolders()
  const folderName = useMemo(() => new Map((folders ?? []).map((f) => [f.id, f.name])), [folders])
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
