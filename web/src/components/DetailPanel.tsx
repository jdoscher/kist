import { useMemo, useState } from 'react'
import { api, fileUrl, type Item } from '../api'
import { useCurrentItems, useFolderNames, useFolders, useItem, useStats } from '../data'
import { fmtBytes } from '../format'
import { useAppState } from '../state'
import { useDraftField, useItemWrite } from '../useWrite'
import { ItemMeta } from './ItemMeta'
import { ItemStatus } from './ItemStatus'

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
  // every edit: in order, applied in place; a failure shows as a notice
  // with Retry, and typed text stays as a draft until the server has it
  const { write, busy } = useItemWrite(item.id)
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
        {busy() && <span className="saving">saving…</span>}
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
