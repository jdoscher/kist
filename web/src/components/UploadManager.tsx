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
import { useAppState, useInvalidateItems } from '../state'
import { nextQueued, uploadsReducer } from '../uploads'

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
  const invalidate = useInvalidateItems()
  const { query } = useAppState()
  const queryRef = useRef(query)
  queryRef.current = query
  const busy = useRef(false)

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
        dispatch({ type: 'fail', key, error: String((err as Error).message ?? err) })
      }
      invalidate()
    })()
  }, [entries, invalidate])

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

  const restore = async (key: number, itemId: string) => {
    try {
      await api.restoreItem(itemId)
      dispatch({ type: 'restored', key, at: Date.now() })
      invalidate()
    } catch (err) {
      window.alert(`Couldn't restore it: ${String((err as Error).message ?? err)}`)
    }
  }

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
      {entries.length > 0 && (
        <div className="upload-toasts">
          {entries.map((e) => (
            <div key={e.key} className={`toast ${e.status}`}>
              <span className="name">{e.name}</span>
              {e.status === 'uploading' &&
                (e.isUrl ? <span>downloading…</span> : <progress value={e.pct} max={100} />)}
              {e.status === 'queued' && <span>queued</span>}
              {e.status === 'done' && <span>✓</span>}
              {e.status === 'duplicate' && <span>already in library</span>}
              {e.status === 'inTrash' && (
                <>
                  <span>already in library, in Trash</span>
                  <button onClick={() => e.itemId && restore(e.key, e.itemId)}>Restore</button>
                  <button
                    aria-label="Dismiss"
                    onClick={() => dispatch({ type: 'dismiss', key: e.key })}
                  >
                    ✕
                  </button>
                </>
              )}
              {e.status === 'error' && (
                <>
                  <span className="error">{e.error}</span>
                  <button onClick={() => dispatch({ type: 'retry', key: e.key })}>Retry</button>
                  <button
                    aria-label="Dismiss"
                    onClick={() => dispatch({ type: 'dismiss', key: e.key })}
                  >
                    ✕
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </Ctx.Provider>
  )
}
