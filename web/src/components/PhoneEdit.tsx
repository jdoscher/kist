// Phone editing (Kist 2.0 Milestone 6, W2.1): the controls the item sheet
// and the full-screen preview get on a phone for a token that may edit:
// stars, tags (add with autocomplete, remove), Move to… and Trash with
// Undo. Every write follows the app's rules: per item in order
// (useItemWrite → serialize), only the change is sent (PATCH tags/folders
// {add, remove}), failures as a notice with Retry (describeError), and
// Undo through the batch history. Notes, rename and folder management stay
// on the desktop. Logic in phoneEdit.ts.

import { FolderInput, Star, Tag, Trash2, X } from 'lucide-react'
import { useCallback, useId, useMemo, useState } from 'react'
import { api, newBatchId, type Item } from '../api'
import { trashedText } from '../bulk'
import { useAfterUndo, useApplyItems, useFolderNames, useTags } from '../data'
import { undoReport } from '../history'
import {
  completeFragment,
  folderChange,
  moveDoneText,
  parseRecent,
  parseTagInput,
  pushRecent,
  RECENT_FOLDERS_KEY,
  tagSuggestions,
  type MoveMode,
} from '../phoneEdit'
import { useAppState } from '../state'
import { useItemWrite, useWrite } from '../useWrite'
import { describeError } from '../writes'
import { Modal } from './Dialog'
import { TagText } from './ItemAI'
import { MoveToSheet } from './MoveTo'
import { useNotices } from './Notices'

/** Five big toggle buttons: tap a star to rate, tap the same one to clear. */
export function PhoneStars({ item }: { item: Item }) {
  const { write } = useItemWrite(item.id)
  const value = item.rating
  return (
    <div className="phone-stars" role="group" aria-label={`Rating: ${value ? `${value} of 5` : 'none'}`}>
      {[1, 2, 3, 4, 5].map((s) => (
        <button
          key={s}
          className={s <= value ? 'on' : ''}
          aria-label={`${s} star${s > 1 ? 's' : ''}`}
          aria-pressed={s <= value}
          onClick={() => {
            const rating = s === value ? 0 : s
            void write('rating', () => api.patchItem(item.id, { rating }))
          }}
        >
          <Star size={22} strokeWidth={1.75} fill={s <= value ? 'currentColor' : 'none'} aria-hidden="true" />
        </button>
      ))}
    </div>
  )
}

/** The item's tags with ✕ to remove, and a field that adds (comma-separated)
 * with matching existing tags offered as you type. */
export function PhoneTags({ item, autoFocus = false }: { item: Item; autoFocus?: boolean }) {
  const { write, busy } = useItemWrite(item.id)
  const { data: allTags } = useTags()
  const [text, setText] = useState('')
  const uid = useId()
  const suggestions = useMemo(() => tagSuggestions(allTags ?? [], text, item.tags), [allTags, text, item.tags])

  const add = async (value = text) => {
    const tags = parseTagInput(value).filter((t) => !item.tags.includes(t))
    setText('')
    if (!tags.length) return
    const ok = await write('tags', () => api.patchTags(item.id, { add: tags }))
    if (!ok) setText(value) // what was typed comes back to try again
  }
  const remove = (tag: string) => void write('tags', () => api.patchTags(item.id, { remove: [tag] }))

  return (
    <div className="detail-section phone-tags">
      <label htmlFor={`${uid}-tag`} className="section-label">
        Tags
      </label>
      {item.tags.length > 0 && (
        <ul className="tag-chips" aria-label="Tags on this item">
          {item.tags.map((t) => (
            <li key={t} className="chip">
              <TagText tag={t} />
              <button aria-label={`Remove tag ${t}`} onClick={() => remove(t)}>
                <X size={14} strokeWidth={2} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="phone-tag-add"
        onSubmit={(e) => {
          e.preventDefault()
          void add()
        }}
      >
        <input
          id={`${uid}-tag`}
          value={text}
          autoFocus={autoFocus}
          autoCapitalize="none"
          autoCorrect="off"
          enterKeyHint="done"
          placeholder="Add a tag"
          aria-describedby={`${uid}-hint`}
          onChange={(e) => setText(e.target.value)}
        />
        <button type="submit" disabled={!text.trim() || busy('tags')}>
          Add
        </button>
        <span id={`${uid}-hint`} className="sr-only">
          Separate several tags with commas. Matching tags are listed below the field.
        </span>
      </form>
      {suggestions.length > 0 && (
        <ul className="phone-tag-suggest" aria-label="Matching tags">
          {suggestions.map((s) => (
            <li key={s}>
              <button aria-label={`Add tag ${s}`} onClick={() => void add(completeFragment(text, s))}>
                <TagText tag={s} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function readRecent(known?: Set<string>): string[] {
  try {
    return parseRecent(localStorage.getItem(RECENT_FOLDERS_KEY), known)
  } catch {
    return []
  }
}

function rememberFolder(id: string) {
  try {
    localStorage.setItem(RECENT_FOLDERS_KEY, JSON.stringify(pushRecent(readRecent(), id)))
  } catch {
    /* private mode */
  }
}

/** Undo for one batch, with the app's wording (and Retry if it fails). */
function useUndoBatch() {
  const afterUndo = useAfterUndo()
  const { notify, dismiss } = useNotices()
  const folderName = useFolderNames()
  return useCallback(
    function undo(batch: string, key: string, verb: string, after?: () => void) {
      api.undoBatch(batch).then(
        (res) => {
          afterUndo()
          const rep = undoReport(res, verb, { folder: (id) => folderName.get(id) })
          if (rep.tone === 'error') notify({ key, tone: 'error', message: rep.message })
          else {
            dismiss(key)
            after?.()
          }
        },
        (e) =>
          notify({
            key,
            tone: 'error',
            message: `Couldn't undo: ${describeError(e)}`,
            action: { label: 'Retry', run: () => undo(batch, key, verb, after) },
          }),
      )
    },
    [afterUndo, notify, dismiss, folderName],
  )
}

/** Move to… (the target becomes the only folder) or Also in (one more):
 * one PATCH folders {add, remove}, then a notice with Undo. */
export function useMoveItem(item: Item) {
  const { write } = useItemWrite(item.id)
  const { notify } = useNotices()
  const folderName = useFolderNames()
  const undo = useUndoBatch()
  return (folderId: string, mode: MoveMode) => {
    rememberFolder(folderId)
    const name = folderName.get(folderId) ?? 'the folder'
    const change = folderChange(mode, item.folder_ids, folderId)
    if (!change) {
      notify({ key: 'move', message: `Already in “${name}”.` })
      return
    }
    const batch = newBatchId()
    void write(
      'folders',
      () => api.patchFolders(item.id, change, batch),
      () =>
        notify({
          key: 'move',
          message: moveDoneText(mode, name, change.remove.length),
          action: { label: 'Undo', run: () => undo(batch, 'move', 'Undid') },
        }),
    )
  }
}

/** Trash with an Undo notice (the trash is one batch: Undo reverts it and
 * opens the item again). */
export function useTrashItem() {
  const w = useWrite()
  const apply = useApplyItems()
  const { notify } = useNotices()
  const { selectOnly } = useAppState()
  const undo = useUndoBatch()
  const trash = (item: Item, onTrashed?: () => void) => {
    const batch = newBatchId()
    return w.run(`trash:${item.id}`, () => api.trashItem(item.id, batch), {
      serializeKey: item.id,
      what: 'move it to Trash',
      onDone: (r) => {
        apply([r.item])
        onTrashed?.()
        notify({
          key: 'trash',
          message: trashedText(1),
          action: { label: 'Undo', run: () => undo(batch, 'trash', 'Restored', () => selectOnly(item.id)) },
        })
      },
    })
  }
  return { trash, busy: (id: string) => w.busy(`trash:${id}`) }
}

/** The folders it's in (✕ to take it out), Move to…, and Trash. */
export function PhoneItemEdit({ item }: { item: Item }) {
  const { write } = useItemWrite(item.id)
  const folderName = useFolderNames()
  const { clearSelection } = useAppState()
  const move = useMoveItem(item)
  const { trash, busy } = useTrashItem()
  const [moving, setMoving] = useState(false)

  return (
    <>
      <div className="phone-actions">
        <button onClick={() => setMoving(true)}>
          <FolderInput size={16} strokeWidth={1.75} aria-hidden="true" /> Move to…
        </button>
        {!item.protected && item.state !== 'trashed' && (
          <button className="danger" disabled={busy(item.id)} onClick={() => void trash(item, clearSelection)}>
            <Trash2 size={16} strokeWidth={1.75} aria-hidden="true" /> Trash
          </button>
        )}
      </div>
      <PhoneTags item={item} />
      {item.folder_ids.length > 0 && (
        <div className="detail-section">
          <h3 className="section-label">Folders</h3>
          <ul className="tag-chips" aria-label="Folders it’s in">
            {item.folder_ids.map((fid) => (
              <li key={fid} className="chip folder-chip">
                {folderName.get(fid) ?? fid}
                <button
                  aria-label={`Remove from folder ${folderName.get(fid) ?? fid}`}
                  onClick={() => void write('folders', () => api.patchFolders(item.id, { remove: [fid] }))}
                >
                  <X size={14} strokeWidth={2} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {moving && <MoveToSheet item={item} recent={readRecent()} onPick={move} onClose={() => setMoving(false)} />}
    </>
  )
}

/** The preview's edit bar on a phone: stars along the top, Tags / Move /
 * Trash along the bottom (between the previous/next arrows). */
export function PhoneEditBar({ item, onTrashed }: { item: Item; onTrashed: () => void }) {
  const move = useMoveItem(item)
  const { trash, busy } = useTrashItem()
  const [sheet, setSheet] = useState<'tags' | 'move' | null>(null)
  return (
    <div className="phone-edit" onClick={(e) => e.stopPropagation()}>
      <div className="phone-edit-top">
        <PhoneStars item={item} />
      </div>
      <div className="phone-edit-bottom" role="group" aria-label="Edit this item">
        <button onClick={() => setSheet('tags')} aria-label={`Tags (${item.tags.length})`}>
          <Tag size={18} strokeWidth={1.75} aria-hidden="true" />
          <span aria-hidden="true">Tags</span>
        </button>
        <button onClick={() => setSheet('move')} aria-label="Move to…">
          <FolderInput size={18} strokeWidth={1.75} aria-hidden="true" />
          <span aria-hidden="true">Move</span>
        </button>
        {!item.protected && item.state !== 'trashed' && (
          <button aria-label="Trash" disabled={busy(item.id)} onClick={() => void trash(item, onTrashed)}>
            <Trash2 size={18} strokeWidth={1.75} aria-hidden="true" />
            <span aria-hidden="true">Trash</span>
          </button>
        )}
      </div>
      {sheet === 'tags' && (
        <Modal title="Tags" className="phone-sheet" onClose={() => setSheet(null)}>
          <PhoneTags item={item} />
        </Modal>
      )}
      {sheet === 'move' && <MoveToSheet item={item} recent={readRecent()} onPick={move} onClose={() => setSheet(null)} />}
    </div>
  )
}
