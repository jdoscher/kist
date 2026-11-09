import { useMemo, useState } from 'react'
import { bulkReport, hardDeleteQuestion, useBulkAction, type BulkAction } from '../bulk'
import { useFolders } from '../data'
import { useAppState } from '../state'
import { useShortcut } from '../useShortcuts'
import { useDialog } from './Dialog'
import { useNotices } from './Notices'

// actions that take the items out of this view: clear the selection after
const LEAVES_VIEW = new Set(['trash', 'restore', 'hardDelete'])

export function BulkBar() {
  const { query, selection, clearSelection, selectAll } = useAppState()
  const { data: folders } = useFolders()
  const bulk = useBulkAction()
  const dialog = useDialog()
  const { notify, dismiss } = useNotices()
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
            notify({
              key: 'bulk',
              tone: 'error',
              message: bulkReport(r),
              action: { label: 'Retry', run: () => run(action, failedIds) },
            })
          } else if (LEAVES_VIEW.has(action.kind)) {
            clearSelection()
          }
        },
      },
    )
  }

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
  // with the preview or a dialog open the key does nothing.
  useShortcut(
    'grid',
    (e) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return false
      e.preventDefault()
      if (query.deleted) void hardDelete()
      else run({ kind: 'trash' })
      return true
    },
    ids.length > 0,
  )

  if (ids.length === 0) return null

  const addTags = () => {
    const tags = tagText.split(',').map((t) => t.trim()).filter(Boolean)
    if (tags.length) {
      run({ kind: 'addTags', tags })
      setTagText('')
    }
  }

  const busy = bulk.isPending

  return (
    <div className="bulkbar">
      <span className="sel-count">{ids.length} selected</span>

      {!query.deleted && (
        <>
          <input
            placeholder="Add tags (comma-sep)"
            value={tagText}
            onChange={(e) => setTagText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addTags()}
          />
          <button onClick={addTags} disabled={busy || !tagText.trim()}>
            Tag
          </button>

          <select
            value=""
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

          <button className="danger" disabled={busy} onClick={() => run({ kind: 'trash' })}>
            Trash
          </button>
        </>
      )}

      {query.deleted && (
        <>
          <button disabled={busy} onClick={() => run({ kind: 'restore' })}>
            Restore
          </button>
          <button className="danger" disabled={busy} onClick={() => void hardDelete()}>
            Delete forever
          </button>
        </>
      )}

      <button onClick={clearSelection} aria-label="Clear selection">
        ✕
      </button>
      {busy && <span className="working">working…</span>}
    </div>
  )
}
