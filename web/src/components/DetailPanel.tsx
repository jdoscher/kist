import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, emptyQuery, fileUrl, thumbUrl, type Item } from '../api'
import { fmtBytes, fmtDuration, linkableUrl } from '../format'
import { swipeDirection } from '../mobile'
import { clearDraft, describeError, draftKey, getDraft, serialize, setDraft } from '../writes'
import { useApplyItems, useCurrentItems, useFolderNames, useFolders, useItem, useStats } from '../data'
import { useAppState } from '../state'


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
  const apply = useApplyItems()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = async (fn: () => Promise<Item>) => {
    setBusy(true)
    setError('')
    try {
      apply([await serialize(item.id, fn)])
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  // while a preview is being made, check this one item (not the whole list)
  const pending = item.preview_status === 'pending'
  useEffect(() => {
    if (!pending) return
    const t = setInterval(async () => {
      try {
        const fresh = await api.getItem(item.id)
        if (fresh.preview_status !== 'pending') apply([fresh])
      } catch {
        /* try again next tick */
      }
    }, 3000)
    return () => clearInterval(t)
  }, [pending, item.id, apply])

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

/**
 * Every edit to one item: goes out in order (serialize), says when it's
 * saving and when it failed, and offers Retry. Typed text that hasn't
 * reached the server is kept as a draft until it has.
 */
function useItemWrite(itemId: string) {
  const apply = useApplyItems()
  const [saving, setSaving] = useState<Set<string>>(new Set())
  const [failure, setFailure] = useState<{ field: string; message: string; retry: () => void } | null>(
    null,
  )
  const write = useCallback(
    async (field: string, fn: () => Promise<Item>, onSaved?: () => void): Promise<boolean> => {
      setSaving((s) => new Set(s).add(field))
      try {
        apply([await serialize(itemId, fn)])
        setFailure((f) => (f?.field === field ? null : f))
        onSaved?.()
        return true
      } catch (e) {
        setFailure({ field, message: describeError(e), retry: () => void write(field, fn, onSaved) })
        return false
      } finally {
        setSaving((s) => {
          const n = new Set(s)
          n.delete(field)
          return n
        })
      }
    },
    [itemId, apply],
  )
  return { write, saving, failure, dismiss: () => setFailure(null) }
}

/** A text field saved on blur that never loses what you typed. */
function useDraftField(itemId: string, field: string, serverValue: string) {
  const key = draftKey(itemId, field)
  const [value, setValue] = useState(() => getDraft(key) ?? serverValue)
  // follow the server value unless there's unsaved text
  useEffect(() => {
    if (getDraft(key) === undefined) setValue(serverValue)
  }, [key, serverValue])
  const change = (v: string) => {
    setValue(v)
    if (v === serverValue) clearDraft(key)
    else setDraft(key, v)
  }
  return { value, change, dirty: value !== serverValue, saved: () => clearDraft(key) }
}

const FIELD_LABEL: Record<string, string> = {
  filename: 'the name',
  notes: 'the notes',
  rating: 'the rating',
  tags: 'the tags',
  folders: 'the folders',
}

function SingleItem({ item }: { item: Item }) {
  const { write, saving, failure, dismiss } = useItemWrite(item.id)
  const { data: folders } = useFolders()
  const folderName = useFolderNames()
  const filename = useDraftField(item.id, 'filename', item.filename)
  const notes = useDraftField(item.id, 'notes', item.notes)
  const [tagText, setTagText] = useState('')

  const saveFilename = () => {
    const v = filename.value.trim()
    if (!v || !filename.dirty) return
    void write('filename', () => api.patchItem(item.id, { filename: v }), filename.saved)
  }
  const saveNotes = () => {
    if (!notes.dirty) return
    const v = notes.value
    void write('notes', () => api.patchItem(item.id, { notes: v }), notes.saved)
  }

  // tags and folders send only the change, never the whole list, so a
  // second click or another writer (an agent) can't be overwritten
  const addTags = async () => {
    const extra = tagText.split(',').map((t) => t.trim()).filter(Boolean)
    if (!extra.length) return
    const text = tagText
    setTagText('')
    const ok = await write('tags', () => api.patchTags(item.id, { add: extra }))
    if (!ok) setTagText(text)
  }
  const removeTag = (tag: string) => write('tags', () => api.patchTags(item.id, { remove: [tag] }))

  return (
    <>
      <ItemStatus item={item} />
      {failure && (
        <div className="save-error" role="alert">
          <span>Couldn't save {FIELD_LABEL[failure.field] ?? failure.field}: {failure.message}</span>
          <button onClick={failure.retry}>Retry</button>
          <button aria-label="Dismiss" onClick={dismiss}>
            ✕
          </button>
        </div>
      )}
      <input
        className={`detail-filename${filename.dirty ? ' unsaved' : ''}`}
        value={filename.value}
        onChange={(e) => filename.change(e.target.value)}
        onBlur={saveFilename}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />

      <div className="stars-row">
        <Stars
          value={item.rating}
          onChange={(rating) => void write('rating', () => api.patchItem(item.id, { rating }))}
        />
        {saving.size > 0 && <span className="saving">saving…</span>}
      </div>

      <div className="detail-section">
        <label>Tags</label>
        <div className="tag-chips">
          {item.tags.map((t) => (
            <span key={t} className="chip">
              #{t}
              <button onClick={() => void removeTag(t)}>×</button>
            </span>
          ))}
        </div>
        <input
          placeholder="Add tags, comma-sep, Enter"
          value={tagText}
          onChange={(e) => setTagText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void addTags()}
        />
      </div>

      <div className="detail-section">
        <label>Folders</label>
        <div className="tag-chips">
          {item.folder_ids.map((fid) => (
            <span key={fid} className="chip folder-chip">
              {folderName.get(fid) ?? fid}
              <button
                onClick={() =>
                  void write('folders', () => api.patchFolders(item.id, { remove: [fid] }))
                }
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <select
          value=""
          onChange={(e) => {
            const fid = e.target.value
            if (fid && !item.folder_ids.includes(fid)) {
              void write('folders', () => api.patchFolders(item.id, { add: [fid] }))
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
        <label>
          Notes{notes.dirty && <span className="unsaved-note"> · not saved yet</span>}
        </label>
        <textarea
          rows={4}
          value={notes.value}
          onChange={(e) => notes.change(e.target.value)}
          onBlur={saveNotes}
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
  const source = linkableUrl(item.source_url)
  return (
    <>
      <div className="detail-section meta-list">
        <div><span>Type</span><span>{item.mime}</span></div>
        <div><span>Size</span><span>{fmtBytes(item.size_bytes)}</span></div>
        {item.width != null && (
          <div><span>Dimensions</span><span>{item.width}×{item.height}</span></div>
        )}
        {item.duration_s != null && (
          <div><span>Duration</span><span>{fmtDuration(item.duration_s)}</span></div>
        )}
        <div><span>Added</span><span>{new Date(item.created_at).toLocaleString()}</span></div>
        {item.source_url && (
          <div>
            <span>Source</span>
            {source ? (
              <a href={source.href} target="_blank" rel="noreferrer">
                {source.host} ↗
              </a>
            ) : (
              // not an http(s) URL: show it, don't link it (and never crash)
              <span className="source-text">{item.source_url}</span>
            )}
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
  const { selection, panelOpen } = useAppState()
  const { items } = useCurrentItems()
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
        single ? <SingleItem key={single.id} item={single} /> : <div className="hint">Loading…</div>
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
