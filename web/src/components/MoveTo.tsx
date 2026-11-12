// "Move to…" on a phone (Kist 2.0 Milestone 6, W2.1): a bottom sheet with
// a search field, the five folders used last as chips, then the whole
// folder tree. "Move" makes the picked folder the item's only one; "Also
// in" adds it. The write itself is the caller's (useMoveItem in
// PhoneEdit.tsx); logic in phoneEdit.ts.

import { Check } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import type { Item } from '../api'
import { useFolders } from '../data'
import { folderRows, type MoveMode } from '../phoneEdit'
import { Modal } from './Dialog'

export function MoveToSheet({
  item,
  recent,
  onPick,
  onClose,
}: {
  item: Item
  /** recent folder ids, newest first */
  recent: string[]
  onPick: (folderId: string, mode: MoveMode) => void
  onClose: () => void
}) {
  const { data: folders, isPending, isError } = useFolders()
  const [search, setSearch] = useState('')
  const [mode, setMode] = useState<MoveMode>('move')
  const uid = useId()
  const rows = useMemo(() => folderRows(folders ?? [], search), [folders, search])
  const byId = useMemo(() => new Map((folders ?? []).map((f) => [f.id, f])), [folders])
  const recents = recent.filter((id) => byId.has(id))
  const pick = (id: string) => {
    onClose()
    onPick(id, mode)
  }
  const inIt = (id: string) => item.folder_ids.includes(id)

  return (
    <Modal title="Move to…" className="phone-sheet move-sheet" onClose={onClose}>
      <fieldset className="move-mode">
        <legend className="sr-only">When you pick a folder</legend>
        <label className={mode === 'move' ? 'on' : ''}>
          <input type="radio" name={`${uid}-mode`} checked={mode === 'move'} onChange={() => setMode('move')} />
          Move
        </label>
        <label className={mode === 'also' ? 'on' : ''}>
          <input type="radio" name={`${uid}-mode`} checked={mode === 'also'} onChange={() => setMode('also')} />
          Also in
        </label>
      </fieldset>
      <p className="move-explain" id={`${uid}-explain`}>
        {mode === 'move'
          ? 'It leaves the folders it’s in now and goes into the one you pick.'
          : 'It stays in its folders and is added to the one you pick.'}
      </p>
      <label htmlFor={`${uid}-search`} className="sr-only">
        Search folders
      </label>
      <input
        id={`${uid}-search`}
        type="search"
        className="move-search"
        placeholder="Search folders"
        autoCapitalize="none"
        autoCorrect="off"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {!search && recents.length > 0 && (
        <div className="move-recent">
          <h4 className="section-label" id={`${uid}-recent`}>
            Recent
          </h4>
          <ul className="tag-chips" aria-labelledby={`${uid}-recent`}>
            {recents.map((id) => (
              <li key={id}>
                <button className="chip folder-chip" aria-describedby={`${uid}-explain`} onClick={() => pick(id)}>
                  {inIt(id) && <Check size={14} aria-label="(it’s in this folder)" role="img" />}
                  {byId.get(id)?.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {isPending ? (
        <p role="status">Loading folders…</p>
      ) : isError ? (
        <p role="alert">Couldn’t load the folders.</p>
      ) : rows.length === 0 ? (
        <p role="status">{search ? 'No folder matches.' : 'No folders yet: make them on a computer.'}</p>
      ) : (
        <ul className="move-tree" aria-label={search ? 'Matching folders' : 'All folders'}>
          {rows.map((r) => (
            <li key={r.id}>
              <button
                style={{ paddingLeft: 12 + r.depth * 18 }}
                aria-describedby={`${uid}-explain`}
                aria-label={`${search ? r.path : r.name}${inIt(r.id) ? ' (it’s in this folder)' : ''}`}
                onClick={() => pick(r.id)}
              >
                <span className="move-name">{search ? r.path : r.name}</span>
                {inIt(r.id) && <Check size={16} aria-hidden="true" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
