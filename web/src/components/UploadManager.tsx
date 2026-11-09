import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, uploadFile } from '../api'
import { jobLabel, jobsFromDataTransfer, type AddJob } from '../clipboard'
import { useApplyItems, useRefresh } from '../data'
import { useAppState } from '../state'
import { nextQueued, uploadsReducer } from '../uploads'
import { useWrite } from '../useWrite'
import { describeError } from '../writes'

interface UploadsApi {
  enqueue: (files: File[]) => void
  addJobs: (jobs: AddJob[]) => void
}

const Ctx = createContext<UploadsApi | null>(null)

export function useUploads(): UploadsApi {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useUploads outside provider')
  return ctx
}

let nextKey = 1

export function UploadManager({ children }: { children: ReactNode }) {
  const [entries, dispatch] = useReducer(uploadsReducer<AddJob>, [])
  const [dragging, setDragging] = useState(false)
  const refresh = useRefresh()
  const apply = useApplyItems()
  const w = useWrite()
  const { query } = useAppState()
  const queryRef = useRef(query)
  queryRef.current = query
  const busy = useRef(false)
  // one list refresh after a batch, not one per file (each refresh reloads
  // the loaded pages of the list on screen; new items' place in the sort
  // order is the server's call)
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshSoon = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current)
    refreshTimer.current = setTimeout(() => {
      if (!busy.current) refresh.itemsAdded()
      else refreshSoon()
    }, 1500)
  }, [refresh]) // eslint-disable-line react-hooks/exhaustive-deps

  // send the next queued entry, one at a time (states: uploads.ts)
  useEffect(() => {
    const next = nextQueued(entries)
    if (!next || busy.current) return
    busy.current = true
    const { key, job, folderId } = next
    dispatch({ type: 'start', key })
    const onProgress = (pct: number) => dispatch({ type: 'progress', key, pct })
    void (async () => {
      try {
        let res
        if (job.kind === 'file') {
          res = await uploadFile(job.file, folderId, onProgress)
        } else {
          try {
            res = await api.addUrl(job.url, folderId)
          } catch (err) {
            if (!job.fallback) throw err
            dispatch({ type: 'fallback', key })
            res = await uploadFile(job.fallback, folderId, onProgress, job.url)
          }
        }
        busy.current = false
        dispatch({
          type: 'finish',
          key,
          created: res.created,
          itemId: res.item.id,
          itemState: res.item.state,
          at: Date.now(),
        })
      } catch (err) {
        busy.current = false
        dispatch({ type: 'fail', key, error: describeError(err) })
      }
      refreshSoon()
    })()
  }, [entries, refreshSoon])

  // finished uploads fade out after a few seconds
  const fading = entries.some((e) => e.status === 'done' || e.status === 'duplicate')
  useEffect(() => {
    if (!fading) return
    const t = setInterval(() => dispatch({ type: 'expire', now: Date.now(), afterMs: 4000 }), 1000)
    return () => clearInterval(t)
  }, [fading])

  const addJobs = useCallback((jobs: AddJob[]) => {
    // the destination is decided now, not when the file's turn comes
    const q = queryRef.current
    const folderId = !q.deleted && q.folder && q.folder !== 'none' ? q.folder : null
    dispatch({
      type: 'add',
      entries: jobs.map((job) => ({
        key: nextKey++,
        job,
        name: jobLabel(job),
        isUrl: job.kind === 'url',
        folderId,
      })),
    })
  }, [])

  const restore = (key: number, itemId: string) =>
    void w.run(`restore:${key}`, () => api.restoreItem(itemId), {
      what: 'restore it',
      serializeKey: itemId,
      onDone: (item) => {
        dispatch({ type: 'restored', key, at: Date.now() })
        // patch it where it's loaded (drops it from Trash; refreshes the
        // counts), then reload just the list on screen so it shows up where
        // it now belongs (itemsAdded would refetch the counts a second time)
        apply([item])
        refresh.items()
      },
    })

  const enqueue = useCallback(
    (files: File[]) => addJobs(files.map((file) => ({ kind: 'file', file }))),
    [addJobs],
  )

  // window-level file drag & drop
  useEffect(() => {
    let depth = 0
    const hasFiles = (e: DragEvent) => e.dataTransfer?.types.includes('Files')
    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return
      depth++
      setDragging(true)
    }
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return
      depth = Math.max(0, depth - 1)
      if (depth === 0) setDragging(false)
    }
    const onDragOver = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault()
    }
    const onDrop = (e: DragEvent) => {
      depth = 0
      setDragging(false)
      if (!hasFiles(e)) return
      e.preventDefault()
      const files = Array.from(e.dataTransfer?.files ?? [])
      if (files.length) enqueue(files)
    }
    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('drop', onDrop)
    }
  }, [enqueue])

  // paste anywhere outside a text field: images, or image/page URLs
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const target = e.target as HTMLElement
      if (target.closest('input, textarea, [contenteditable]')) return
      const jobs = jobsFromDataTransfer(e.clipboardData)
      if (jobs.length) {
        e.preventDefault()
        addJobs(jobs)
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [addJobs])

  return (
    <Ctx.Provider value={{ enqueue, addJobs }}>
      {children}
      {dragging && (
        <div className="drop-overlay">
          <div>Drop files to add them to the library</div>
        </div>
      )}
      {/* always mounted: a live region must exist before its text changes */}
      <div className="upload-toasts" role="status" aria-live="polite" aria-relevant="additions text" data-keep-live="">
          {entries.map((e) => (
            <div key={e.key} className={`toast ${e.status}`}>
              <span className="name">{e.name}</span>
              {e.status === 'uploading' &&
                (e.isUrl ? (
                  <span>downloading…</span>
                ) : (
                  <progress value={e.pct} max={100} aria-label={`Uploading ${e.name}`} />
                ))}
              {e.status === 'queued' && <span>queued</span>}
              {e.status === 'done' && (
                <span>
                  <span aria-hidden="true">✓</span>
                  <span className="sr-only">added</span>
                </span>
              )}
              {e.status === 'duplicate' && <span>already in library</span>}
              {e.status === 'inTrash' && (
                <>
                  <span>already in library, in Trash</span>
                  <button disabled={w.busy(`restore:${e.key}`)} onClick={() => e.itemId && restore(e.key, e.itemId)}>
                    Restore
                  </button>
                  <button
                    aria-label={`Dismiss ${e.name}`}
                    onClick={() => dispatch({ type: 'dismiss', key: e.key })}
                  >
                    <span aria-hidden="true">✕</span>
                  </button>
                </>
              )}
              {e.status === 'error' && (
                <>
                  <span className="error" role="alert">
                    {e.error}
                  </span>
                  <button onClick={() => dispatch({ type: 'retry', key: e.key })}>Retry</button>
                  <button
                    aria-label={`Dismiss ${e.name}`}
                    onClick={() => dispatch({ type: 'dismiss', key: e.key })}
                  >
                    <span aria-hidden="true">✕</span>
                  </button>
                </>
              )}
            </div>
          ))}
      </div>
    </Ctx.Provider>
  )
}
