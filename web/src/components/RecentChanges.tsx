import { useEffect, useRef, useState } from 'react'
import { api, type Batch } from '../api'
import { useAfterUndo, useBatches, useFolderNames } from '../data'
import { batchSummary, batchUndoable, timeAgo, undoReport } from '../history'
import { useAppState } from '../state'
import { useWrite } from '../useWrite'
import { Modal } from './Dialog'
import { useNotices } from './Notices'

/** Recent batches of changes (what one bulk action, request, MCP tool call
 * or CLI command did), newest first, each with Undo. Opened from the top
 * bar, the phone menu, or ⌘Z / Ctrl+Z; focus starts on the newest Undo. */
export function RecentChanges() {
  const { recentOpen, setRecentOpen } = useAppState()
  if (!recentOpen) return null
  return <RecentChangesPanel onClose={() => setRecentOpen(false)} />
}

function RecentChangesPanel({ onClose }: { onClose: () => void }) {
  const { data, isLoading, isError, error } = useBatches(true)
  const afterUndo = useAfterUndo()
  const folderName = useFolderNames()
  const { notify } = useNotices()
  const w = useWrite()
  // undone in this session: the server keeps the batch, so mark it here
  const [undone, setUndone] = useState<Record<string, string>>({})
  const listRef = useRef<HTMLOListElement>(null)
  const focused = useRef(false)
  // the list arrives after the panel opens: put focus on the newest Undo
  // then, so ⌘Z, Enter undoes the last change
  useEffect(() => {
    if (!data?.length || focused.current) return
    focused.current = true
    listRef.current?.querySelector<HTMLElement>('button')?.focus()
  }, [data])

  const undo = (b: Batch) =>
    void w.run(`undo:${b.batch_id}`, () => api.undoBatch(b.batch_id), {
      what: 'undo that',
      onDone: (r) => {
        afterUndo()
        setUndone((u) => ({ ...u, [b.batch_id]: r.batch_id }))
        const rep = undoReport(r, 'Undid', { folder: (id) => folderName.get(id) })
        notify({
          key: `undo:${b.batch_id}`,
          tone: rep.tone,
          message: rep.message,
          // the undo is a batch too: undoing it puts the changes back
          action: r.undone ? { label: 'Redo', run: () => redo(r.batch_id, b.batch_id) } : undefined,
        })
      },
    })

  const redo = (undoBatch: string, original: string) =>
    void w.run(`redo:${undoBatch}`, () => api.undoBatch(undoBatch), {
      what: 'redo that',
      onDone: (r) => {
        afterUndo()
        setUndone((u) => {
          const next = { ...u }
          delete next[original]
          return next
        })
        const rep = undoReport(r, 'Redid', { folder: (id) => folderName.get(id) })
        notify({ key: `undo:${original}`, tone: rep.tone, message: rep.message })
      },
    })

  const now = Date.now()
  return (
    <Modal title="Recent changes" onClose={onClose} className="recent-changes">
      <p className="hint">
        Everything changed lately, by you, agents and Kist itself. Undo puts a whole batch back; anything changed again
        since is left alone and listed.
      </p>
      {isLoading && (
        <p className="hint" role="status">
          Loading…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load recent changes: {String((error as Error).message)}</p>}
      {data && data.length === 0 && <p className="hint">No changes yet.</p>}
      {data && data.length > 0 && (
        <ol className="batch-list" ref={listRef}>
          {data.map((b) => {
            const summary = batchSummary(b)
            const can = batchUndoable(b)
            const done = undone[b.batch_id]
            return (
              <li key={b.batch_id} className="batch-row">
                <div className="batch-text">
                  <span className="batch-what">{summary}</span>
                  <span className="batch-when">
                    <time dateTime={b.at} title={new Date(b.at).toLocaleString()}>
                      {timeAgo(b.at, now)}
                    </time>
                    {!can && ' · deleted for good, can’t be undone'}
                    {done && ' · undone'}
                  </span>
                </div>
                {can && !done && (
                  <button
                    disabled={w.busy(`undo:${b.batch_id}`)}
                    onClick={() => undo(b)}
                    aria-label={`Undo: ${summary}`}
                  >
                    Undo
                  </button>
                )}
                {done && (
                  <button disabled={w.busy(`redo:${done}`)} onClick={() => redo(done, b.batch_id)} aria-label={`Redo: ${summary}`}>
                    Redo
                  </button>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </Modal>
  )
}
