import { useEffect, useMemo, useState } from 'react'
import { api, fileUrl, type Item } from '../api'
import { flattenItems, useAppState, useFolders, useInvalidateItems, useItems, useStats } from '../state'

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

      <a className="open-original" href={fileUrl(item)} target="_blank" rel="noreferrer">
        Open original ↗
      </a>
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

  if (!panelOpen) return null

  return (
    <aside className="detail-panel">
      {selected.length === 0 ? (
        <LibrarySummary />
      ) : selected.length === 1 ? (
        <SingleItem item={selected[0]} />
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
