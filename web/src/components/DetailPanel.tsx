import { ExternalLink, Lock, LockOpen, Maximize2, Star, X } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { api, fileUrl, thumbUrl, type Item } from '../api'
import { useCaps, useCurrentItems, useFolderNames, useFolders, useItem, useStats, useVersion } from '../data'
import { fmtBytes } from '../format'
import { useAppState } from '../state'
import { usePresence } from '../motion'
import { useDraftField, useItemWrite, useTextSave } from '../useWrite'
import { ItemHistory } from './ItemHistory'
import { ItemLinks } from './ItemLinks'
import { ItemCutouts } from './cutouts/Cutouts' // F7
import { ItemMeta } from './ItemMeta'
import { ItemSections, OnThisDay } from './m45/lazy'
import { ItemFeedback } from './proofing/lazy'
import { ItemStatus } from './ItemStatus'
import { TYPES } from './TopBar'
import { ReadOnlyItem } from './DetailSheet'
import { CaptionField, ImpliedTags, ItemFaces, MatchWhy, MoreLikeThisButton, PaletteSwatches, StackInfo, TagSuggestions, TagText } from './ItemAI'
import { ItemBoards, ItemCull, ItemVersions } from './m3/ItemCreative'
import { ItemKinds } from './kinds/KindsUI'
import { ItemProjectFields } from './projects/ItemProjectFields'

/** Protected items can't be trashed or deleted. Agents can protect but not
 * unprotect; removing protection needs a full-access (write/admin) token. */
function ProtectToggle({ item }: { item: Item }) {
  const { write, busy } = useItemWrite(item.id)
  const caps = useCaps()
  const on = !!item.protected
  const locked = on && !caps.canDestroy
  return (
    <button
      className={`protect-toggle btn-icon${on ? ' on' : ''}`}
      aria-pressed={on}
      disabled={busy('protected') || locked}
      title={
        locked
          ? 'Protected. Only a full-access token can remove protection.'
          : on
            ? 'Protected: it can’t be trashed or deleted. Press to remove protection.'
            : 'Protect it so it can’t be trashed or deleted'
      }
      onClick={() => void write('protected', () => api.patchItem(item.id, { protected: !on }))}
    >
      {on ? <Lock size={14} aria-hidden="true" /> : <LockOpen size={14} aria-hidden="true" />}
      {on ? 'Protected' : 'Protect'}
    </button>
  )
}

/** Five labelled toggle buttons; filled vs outline stars add a shape cue to the colour. */
function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="stars" role="group" aria-label={`Rating: ${value ? `${value} of 5` : 'none'}`}>
      {[1, 2, 3, 4, 5].map((s) => (
        <button
          key={s}
          className={s <= value ? 'star-lit' : ''}
          aria-label={`${s} star${s > 1 ? 's' : ''}`}
          aria-pressed={s <= value}
          title={s === value ? 'Press again to clear the rating' : `${s} star${s > 1 ? 's' : ''}`}
          onClick={() => onChange(s === value ? 0 : s)}
        >
          <Star size={20} fill={s <= value ? 'currentColor' : 'none'} aria-hidden="true" />
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
          Open <Maximize2 size={14} aria-hidden="true" />
        </button>
      </div>
      <ItemStatus item={item} />
      <MatchWhy item={item} />
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
        <ProtectToggle item={item} />
        <span className="saving" role="status">
          {busy() ? 'saving…' : ''}
        </span>
      </div>
      <MoreLikeThisButton item={item} />
      <CaptionField item={item} editable />

      <ItemCull item={item} />

      <div className="detail-section">
        <label htmlFor={`${uid}-tags`}>Tags</label>
        <div className="tag-chips">
          {item.tags.map((t) => (
            <span key={t} className="chip">
              <TagText tag={t} />
              <button aria-label={`Remove tag ${t}`} title={`Remove tag ${t}`} onClick={() => void removeTag(t)}>
                <X size={14} aria-hidden="true" />
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
        <ImpliedTags item={item} />
        <TagSuggestions item={item} />
      </div>

      <ItemKinds item={item} />

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
                <X size={14} aria-hidden="true" />
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

      {/* F6: the custom fields of the item's projects */}
      <ItemProjectFields item={item} />

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

      <PaletteSwatches item={item} />
      <ItemFaces item={item} editable />
      <StackInfo item={item} editable />
      <ItemLinks item={item} editable />
      <ItemCutouts item={item} />

      <ItemVersions item={item} />
      <ItemBoards item={item} />
      <ItemFeedback item={item} />

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

function LibrarySummary() {
  const { data: stats } = useStats()
  const { canEdit } = useCaps()
  const { data: version } = useVersion()
  return (
    <div className="multi-summary">
      <h2 className="panel-title view-title">No selection</h2>
      <p className="hint-text">
        Click an item to see {canEdit ? 'and edit ' : ''}its details. Double-click (or Enter) opens it full screen.
      </p>
      <OnThisDay />
      {stats && (
        <div className="detail-section">
          <h3 className="section-label">Library</h3>
          <div className="meta-list">
            <div>
              <span>Items</span>
              <span>{stats.total_items.toLocaleString()}</span>
            </div>
            {Object.entries(stats.by_kind)
              .filter(([, n]) => n > 0)
              .sort((a, b) => b[1] - a[1])
              .map(([kind, n]) => (
                <div key={kind}>
                  <span>{TYPES.find(([k]) => k === kind)?.[1] ?? kind}</span>
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
                <span className="mono" title={`commit ${version.commit}`}>
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
  const { selection, panelOpen, density, chromeShown } = useAppState()
  const { items } = useCurrentItems()
  const selected = useMemo(() => items.filter((i) => selection.has(i.id)), [items, selection])
  // one item that isn't in the loaded grid (opened from a link): fetch it
  const onlyId = selection.size === 1 ? [...selection][0] : null
  const single = useItem(onlyId, items)
  const caps = useCaps()

  // gallery density tucks the panel away unless Panels is on; closing
  // fades it out over --dur-out before it unmounts (motion.ts)
  const { mounted, leaving } = usePresence(density === 'gallery' ? chromeShown : panelOpen)
  if (!mounted) return null

  return (
    <aside className={`detail-panel${leaving ? ' leaving' : ''}`} aria-label="Item details" inert={leaving || undefined}>
      {selection.size === 0 ? (
        <LibrarySummary />
      ) : selection.size === 1 ? (
        single ? (
          caps.canEdit ? (
            <SingleItem key={single.id} item={single} />
          ) : (
            <>
              <ReadOnlyItem key={single.id} item={single} withThumb />
              <ItemKinds item={single} />
            </>
          )
        ) : (
          <div className="hint" role="status">Loading…</div>
        )
      ) : (
        <div className="multi-summary">
          <h2 className="panel-title view-title">{selected.length} items selected</h2>
          <p>{fmtBytes(selected.reduce((acc, i) => acc + i.size_bytes, 0))} total</p>
          <p className="hint">Use the bar below for bulk tag / move / trash.</p>
        </div>
      )}
    </aside>
  )
}
