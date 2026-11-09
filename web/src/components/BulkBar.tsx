import { useMemo, useState } from 'react'
import { BULK_VERB, hardDeleteQuestion, useBulkAction, type BulkInput, type BulkResult } from '../bulk'
import { useFolders } from '../data'
import { useAppState } from '../state'
import { useShortcut } from '../useShortcuts'

// actions that take the items out of this view: clear the selection after
const LEAVES_VIEW = new Set(['trash', 'restore', 'hardDelete'])

export function BulkBar() {
  const { query, selection, clearSelection, selectAll } = useAppState()
  const { data: folders } = useFolders()
  const bulk = useBulkAction()
  const [tagText, setTagText] = useState('')
  const [report, setReport] = useState<BulkResult | null>(null)

  const ids = useMemo(() => [...selection], [selection])

  // the report belongs to the failed items it selected; any other selection
  // means the user moved on
  const showReport =
    report !== null &&
    report.failed.length === ids.length &&
    report.failed.every((f) => selection.has(f.id))

  const run = (action: BulkInput['action']) => {
    if (bulk.isPending || ids.length === 0) return
    setReport(null)
    bulk.mutate(
      { ids, action },
      {
        onSuccess: (r) => {
          if (r.failed.length) {
            // keep only what failed selected, with the reason and a Retry
            selectAll(r.failed.map((f) => f.id))
            setReport(r)
          } else if (LEAVES_VIEW.has(action.kind)) {
            clearSelection()
          }
        },
      },
    )
  }

  // Delete key → trash (or delete forever in trash view). Grid layer only:
  // with the preview or a dialog open the key does nothing.
  useShortcut(
    'grid',
    (e) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return false
      e.preventDefault()
      if (query.deleted) {
        if (confirm(hardDeleteQuestion(ids.length))) run({ kind: 'hardDelete' })
      } else {
        run({ kind: 'trash' })
      }
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
      {showReport ? (
        <span className="bulk-report">
          {BULK_VERB[report.input.action.kind]} {report.ok} of {report.ok + report.failed.length}.{' '}
          {report.failed.length} failed: {report.failed[0].error}.{' '}
          <button disabled={busy} onClick={() => run(report.input.action)}>
            Retry
          </button>
        </span>
      ) : (
        <span className="sel-count">{ids.length} selected</span>
      )}

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
          <button
            className="danger"
            disabled={busy}
            onClick={() => {
              if (confirm(hardDeleteQuestion(ids.length))) {
                run({ kind: 'hardDelete' })
              }
            }}
          >
            Delete forever
          </button>
        </>
      )}

      <button onClick={clearSelection}>✕</button>
      {busy && <span className="working">working…</span>}
    </div>
  )
}
