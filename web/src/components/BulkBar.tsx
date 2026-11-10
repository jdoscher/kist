import { Lock, LockOpen, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api } from '../api'
import { bulkReport, hardDeleteQuestion, trashedText, useBulkAction, type BulkAction } from '../bulk'
import { useAfterUndo, useCaps, useFolderNames, useFolders, useRefresh } from '../data'
import { undoReport } from '../history'
import { describeError } from '../writes'
import { useAppState } from '../state'
import { useShortcut } from '../useShortcuts'
import { useDialog } from './Dialog'
import { useNotices } from './Notices'
import { StackActions } from './BulkStack'
import { BulkCreative } from './m3/BulkCreative'

// actions that take the items out of this view: clear the selection after
const LEAVES_VIEW = new Set(['trash', 'restore', 'hardDelete'])

export function BulkBar() {
  const { query, selection, clearSelection, selectAll } = useAppState()
  const { data: folders } = useFolders()
  const bulk = useBulkAction()
  const dialog = useDialog()
  const { notify, dismiss } = useNotices()
  const refresh = useRefresh()
  const afterUndo = useAfterUndo()
  const caps = useCaps()
  const folderName = useFolderNames()
  const [tagText, setTagText] = useState('')

  const ids = useMemo(() => [...selection], [selection])

  const run = (action: BulkAction, targets: string[] = ids) => {
    if (bulk.isPending || targets.length === 0) return
    dismiss('bulk')
    bulk.mutate(
      { ids: targets, action },
      {
        onSuccess: (r) => {
          if (r.failed.length) {
            // keep only what failed selected; the notice says why, with Retry
            const failedIds = r.failed.map((f) => f.id)
            selectAll(failedIds)
            const done = targets.filter((id) => !failedIds.includes(id))
            notify({
              key: 'bulk',
              tone: 'error',
              message: bulkReport(r),
              action: { label: 'Retry', run: () => run(action, failedIds) },
              // the ones that did go to the Trash can still come back
              actions: action.kind === 'trash' && done.length ? [{ label: 'Undo', run: () => undoTrash(done, r.batch) }] : [],
            })
          } else if (LEAVES_VIEW.has(action.kind)) {
            clearSelection()
          }
          // trashing is undoable: the whole action was one batch, so Undo
          // reverts that batch (and says so if something changed since)
          if (action.kind === 'trash' && r.ok > 0 && !r.failed.length) {
            notify({
              key: 'trash',
              message: trashedText(targets.length),
              action: { label: 'Undo', run: () => undoTrash(targets, r.batch) },
            })
          }
        },
      },
    )
  }

  // put trashed items back by undoing the trash batch, then reload the
  // list on screen so they show up where they sort. A server without batch
  // undo (older) gets the items restored one by one instead.
  const undoTrash = (trashed: string[], batch?: string) => {
    if (batch) {
      api.undoBatch(batch).then(
        (res) => {
          afterUndo()
          const rep = undoReport(res, 'Restored', { folder: (id) => folderName.get(id) })
          if (rep.tone === 'error') notify({ key: 'trash', tone: 'error', message: rep.message })
          else {
            dismiss('trash')
            selectAll(trashed)
          }
        },
        (e) => {
          if ((e as { status?: number })?.status === 404) restoreEach(trashed)
          else
            notify({
              key: 'trash',
              tone: 'error',
              message: `Couldn't undo: ${describeError(e)}`,
              action: { label: 'Retry', run: () => undoTrash(trashed, batch) },
            })
        },
      )
      return
    }
    restoreEach(trashed)
  }

  const restoreEach = (trashed: string[]) =>
    bulk.mutate(
      { ids: trashed, action: { kind: 'restore' } },
      {
        onSuccess: (r) => {
          refresh.items()
          if (r.failed.length)
            notify({
              key: 'trash',
              tone: 'error',
              message: bulkReport(r),
              action: { label: 'Retry', run: () => restoreEach(r.failed.map((f) => f.id)) },
            })
          else selectAll(trashed)
        },
      },
    )

  const hardDelete = async () => {
    const n = ids.length
    const ok = await dialog.confirm({
      title: hardDeleteQuestion(n),
      confirmLabel: 'Delete forever',
      danger: true,
    })
    if (ok) run({ kind: 'hardDelete' })
  }

  // Delete key → trash (or delete forever in trash view). Grid layer only:
  // with the preview or a dialog open the key does nothing; and only with
  // focus on the grid (or nowhere), so Backspace on a button can't trash.
  useShortcut(
    'grid',
    (e) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return false
      const t = e.target as HTMLElement | null
      if (t && t !== document.body && !t.closest?.('.grid-scroll')) return false
      e.preventDefault()
      if (query.deleted) {
        if (caps.canDestroy) void hardDelete()
      } else run({ kind: 'trash' })
      return true
    },
    ids.length > 0 && caps.canEdit,
  )

  if (ids.length === 0 || !caps.canEdit) return null

  const addTags = () => {
    const tags = tagText.split(',').map((t) => t.trim()).filter(Boolean)
    if (tags.length) {
      run({ kind: 'addTags', tags })
      setTagText('')
    }
  }

  const busy = bulk.isPending

  return (
    <div className="bulkbar" role="toolbar" aria-label="Selection actions">
      <span className="sel-count">{ids.length} selected</span>

      {!query.deleted && (
        <>
          <input
            placeholder="Add tags (comma-sep)"
            aria-label="Tags to add to the selected items, comma-separated"
            value={tagText}
            onChange={(e) => setTagText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addTags()}
          />
          <button onClick={addTags} disabled={busy || !tagText.trim()}>
            Tag
          </button>

          <select
            value=""
            aria-label="Add the selected items to a folder"
            disabled={busy}
            onChange={(e) => {
              if (e.target.value === '') return
              if (e.target.value === '__none__') run({ kind: 'move', folderId: null })
              else run({ kind: 'addFolder', folderId: e.target.value })
            }}
          >
            <option value="">Add to folder…</option>
            <option value="__none__">(remove from all folders)</option>
            {(folders ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>

          <button
            className="btn-icon"
            disabled={busy}
            onClick={() => run({ kind: 'protect', value: true })}
            title="Protected items can't be trashed or deleted until unprotected"
          >
            <Lock size={14} strokeWidth={1.75} aria-hidden="true" />
            Protect
          </button>
          {caps.canDestroy && (
            <button className="btn-icon" disabled={busy} onClick={() => run({ kind: 'protect', value: false })}>
              <LockOpen size={14} strokeWidth={1.75} aria-hidden="true" />
              Unprotect
            </button>
          )}

          <StackActions disabled={busy} />

          <button className="danger" disabled={busy} onClick={() => run({ kind: 'trash' })}>
            Trash
          </button>
          <BulkCreative ids={ids} busy={busy} />
        </>
      )}

      {query.deleted && (
        <>
          <button disabled={busy} onClick={() => run({ kind: 'restore' })}>
            Restore
          </button>
          {caps.canDestroy && (
            <button className="danger" disabled={busy} onClick={() => void hardDelete()}>
              Delete forever
            </button>
          )}
        </>
      )}

      <button className="icon-btn" onClick={clearSelection} aria-label="Clear selection" title="Clear selection">
        <X size={16} strokeWidth={1.75} aria-hidden="true" />
      </button>
      <span className="working" role="status">
        {busy ? 'working…' : ''}
      </span>
    </div>
  )
}
