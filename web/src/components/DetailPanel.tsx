import { useId, useMemo, useState } from 'react'
import { api, fileUrl, thumbUrl, type Item } from '../api'
import { useCurrentItems, useFolderNames, useFolders, useItem, useStats, useVersion } from '../data'
import { fmtBytes } from '../format'
import { useAppState } from '../state'
import { useDraftField, useItemWrite, useTextSave } from '../useWrite'
import { ItemMeta } from './ItemMeta'
import { ItemStatus } from './ItemStatus'

/** Five labelled toggle buttons; ★/☆ adds a shape cue to the colour. */
function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="stars" role="group" aria-label={`Rating: ${value ? `${value} of 5` : 'none'}`}>
      {[1, 2, 3, 4, 5].map((s) => (
        <button
          key={s}
          className={s <= value ? 'on' : ''}
          aria-label={`${s} star${s > 1 ? 's' : ''}`}
          aria-pressed={s <= value}
          title={s === value ? 'Press again to clear the rating' : `${s} star${s > 1 ? 's' : ''}`}
          onClick={() => onChange(s === value ? 0 : s)}
        >
          <span aria-hidden="true">{s <= value ? '★' : '☆'}</span>
        </button>
      ))}
    </div>
  )
}

function SingleItem({ item }: { item: Item }) {
  // every edit: in order, applied in place; a failure shows as a notice
  // with Retry, and typed text stays as a draft until the server has it
  const { write, busy } = useItemWrite(item.id)
  const saveText = useTextSave(item)
  const { setPreviewId } = useAppState()
  const { data: folders } = useFolders()
  const folderName = useFolderNames()
  const filename = useDraftField(item.id, 'filename', item.filename)
  const notes = useDraftField(item.id, 'notes', item.notes)
  const [tagText, setTagText] = useState('')
  const uid = useId()
  const thumb = thumbUrl(item, 512)

  const saveFilename = () => {
    if (!filename.value.trim() || !filename.dirty) return
    void saveText('filename', filename.value, filename.saved)
  }
  const saveNotes = () => {
    if (!notes.dirty) return
    void saveText('notes', notes.value, notes.saved)
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
      <div className="detail-thumb">
        {thumb ? <img src={thumb} alt="" /> : <div className="detail-thumb-icon" aria-hidden="true">.{item.ext}</div>}
        <button className="open-full" onClick={() => setPreviewId(item.id)} title="Open full screen (Enter, or double-click the tile)">
          Open <span aria-hidden="true">⤢</span>
        </button>
      </div>
      <ItemStatus item={item} />
      <label htmlFor={`${uid}-name`} className="sr-only">
        File name
      </label>
      <input
        id={`${uid}-name`}
        className={`detail-filename${filename.dirty ? ' unsaved' : ''}`}
        value={filename.value}
        aria-describedby={filename.dirty ? `${uid}-name-note` : undefined}
        onChange={(e) => filename.change(e.target.value)}
        onBlur={saveFilename}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
      {filename.dirty && (
        <span id={`${uid}-name-note`} className="unsaved-note">
          Name not saved yet
        </span>
      )}

      <div className="stars-row">
        <Stars
          value={item.rating}
          onChange={(rating) => void write('rating', () => api.patchItem(item.id, { rating }))}
        />
        <span className="saving" role="status">
          {busy() ? 'saving…' : ''}
        </span>
      </div>

      <div className="detail-section">
        <label htmlFor={`${uid}-tags`}>Tags</label>
        <div className="tag-chips">
          {item.tags.map((t) => (
            <span key={t} className="chip">
              #{t}
              <button aria-label={`Remove tag ${t}`} title={`Remove tag ${t}`} onClick={() => void removeTag(t)}>
                <span aria-hidden="true">×</span>
              </button>
            </span>
          ))}
        </div>
        <input
          id={`${uid}-tags`}
          placeholder="Add tags, comma-sep, Enter"
          aria-describedby={`${uid}-tags-hint`}
          value={tagText}
          onChange={(e) => setTagText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void addTags()}
        />
        <span id={`${uid}-tags-hint`} className="sr-only">
          Separate tags with commas; Enter adds them.
        </span>
      </div>

      <div className="detail-section">
        <label htmlFor={`${uid}-folders`}>Folders</label>
        <div className="tag-chips">
          {item.folder_ids.map((fid) => (
            <span key={fid} className="chip folder-chip">
              {folderName.get(fid) ?? fid}
              <button
                aria-label={`Remove from folder ${folderName.get(fid) ?? fid}`}
                title="Remove from this folder"
                onClick={() => void write('folders', () => api.patchFolders(item.id, { remove: [fid] }))}
              >
                <span aria-hidden="true">×</span>
              </button>
            </span>
          ))}
        </div>
        <select
          id={`${uid}-folders`}
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
        <label htmlFor={`${uid}-notes`}>
          Notes{notes.dirty && <span className="unsaved-note"> · not saved yet</span>}
        </label>
        <textarea
          id={`${uid}-notes`}
          rows={4}
          value={notes.value}
          onChange={(e) => notes.change(e.target.value)}
          onBlur={saveNotes}
          placeholder="Notes…"
        />
      </div>

      <ItemMeta item={item} />

      <a className="open-original" href={fileUrl(item)} target="_blank" rel="noreferrer">
        Open original <span aria-hidden="true">↗</span>
        <span className="sr-only"> (new tab)</span>
      </a>
    </>
  )
}

function LibrarySummary() {
  const { data: stats } = useStats()
  const { data: version } = useVersion()
  return (
    <div className="multi-summary">
      <h2 className="panel-title">No selection</h2>
      <p className="hint-text">Click an item to see and edit its details. Double-click (or Enter) opens it full screen.</p>
      {stats && (
        <div className="detail-section">
          <h3 className="section-label">Library</h3>
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
            {version && (
              <div>
                <span>Version</span>
                <span title={`commit ${version.commit}`}>
                  {version.version}
                  {version.commit !== 'unknown' ? ` · ${version.commit.slice(0, 12)}` : ''}
                </span>
              </div>
            )}
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
    <aside className="detail-panel" aria-label="Item details">
      {selection.size === 0 ? (
        <LibrarySummary />
      ) : selection.size === 1 ? (
        single ? <SingleItem key={single.id} item={single} /> : <div className="hint" role="status">Loading…</div>
      ) : (
        <div className="multi-summary">
          <h2 className="panel-title">{selected.length} items selected</h2>
          <p>{fmtBytes(selected.reduce((acc, i) => acc + i.size_bytes, 0))} total</p>
          <p className="hint">Use the bar below for bulk tag / move / trash.</p>
        </div>
      )}
    </aside>
  )
}
