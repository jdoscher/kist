import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, uploadFile } from '../api'
import { jobLabel, jobsFromDataTransfer, type AddJob } from '../clipboard'
import { useAppState, useInvalidateItems } from '../state'

interface UploadEntry {
  key: number
  name: string
  pct: number
  isUrl: boolean
  status: 'queued' | 'uploading' | 'done' | 'duplicate' | 'error'
  error?: string
}

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
  const [entries, setEntries] = useState<UploadEntry[]>([])
  const [dragging, setDragging] = useState(false)
  const invalidate = useInvalidateItems()
  const { query } = useAppState()
  const queryRef = useRef(query)
  queryRef.current = query
  const running = useRef(false)
  const queue = useRef<{ key: number; job: AddJob }[]>([])

  const patch = (key: number, p: Partial<UploadEntry>) =>
    setEntries((prev) => prev.map((e) => (e.key === key ? { ...e, ...p } : e)))

  const pump = useCallback(async () => {
    if (running.current) return
    running.current = true
    while (queue.current.length) {
      const { key, job } = queue.current.shift()!
      patch(key, { status: 'uploading' })
      try {
        const raw = queryRef.current.folder
        const folder = raw === 'none' ? null : raw
        const onProgress = (pct: number) => patch(key, { pct })
        let res
        if (job.kind === 'file') {
          res = await uploadFile(job.file, folder, onProgress)
        } else {
          try {
            res = await api.addUrl(job.url, folder)
          } catch (err) {
            if (!job.fallback) throw err
            patch(key, { isUrl: false })
            res = await uploadFile(job.fallback, folder, onProgress, job.url)
          }
        }
        patch(key, { status: res.created ? 'done' : 'duplicate', pct: 100 })
      } catch (err) {
        patch(key, { status: 'error', error: String((err as Error).message ?? err) })
      }
      invalidate()
    }
    running.current = false
    setTimeout(
      () =>
        setEntries((prev) =>
          prev.filter((e) => e.status === 'error' || e.status === 'uploading' || e.status === 'queued'),
        ),
      4000,
    )
  }, [invalidate])

  const addJobs = useCallback(
    (jobs: AddJob[]) => {
      const fresh = jobs.map((job) => ({ key: nextKey++, job }))
      queue.current.push(...fresh)
      setEntries((prev) => [
        ...prev,
        ...fresh.map(({ key, job }) => ({
          key,
          name: jobLabel(job),
          pct: 0,
          isUrl: job.kind === 'url',
          status: 'queued' as const,
        })),
      ])
      void pump()
    },
    [pump],
  )

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
              {e.status === 'error' && <span className="error">{e.error}</span>}
            </div>
          ))}
        </div>
      )}
    </Ctx.Provider>
  )
}
