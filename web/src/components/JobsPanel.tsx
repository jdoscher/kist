import { useState } from 'react'
import { api, emptyQuery, type Job } from '../api'
import { useAppState, useInvalidateItems, useJobs, useStats } from '../state'

/** Imports that failed (with Retry / Dismiss) and a way to find the items
 * whose preview failed. Opened from "Needs attention" in the sidebar. */
export function JobsPanel() {
  const { jobsOpen, setJobsOpen, setQuery, mobile, closeDrawer } = useAppState()
  const { data: jobs, isLoading } = useJobs(jobsOpen)
  const { data: stats } = useStats()
  const invalidate = useInvalidateItems()
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState('')

  if (!jobsOpen) return null

  const act = async (job: Job, fn: () => Promise<unknown>, done: (r: unknown) => string) => {
    setBusy(job.id)
    setNote('')
    try {
      setNote(done(await fn()))
      invalidate()
    } catch (e) {
      setNote(String((e as Error).message ?? e))
    } finally {
      setBusy(null)
    }
  }

  const showFailedPreviews = () => {
    setJobsOpen(false)
    if (mobile) closeDrawer()
    setQuery({ ...emptyQuery, preview: 'failed' })
  }

  const close = () => setJobsOpen(false)

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Needs attention">
        <div className="modal-head">
          <h3>Needs attention</h3>
          <button onClick={close} aria-label="Close">
            ✕
          </button>
        </div>

        {(stats?.preview_failed ?? 0) > 0 && (
          <div className="attention-block">
            <p>
              {stats!.preview_failed === 1
                ? '1 item has no preview because making it failed.'
                : `${stats!.preview_failed} items have no preview because making them failed.`}
            </p>
            <button onClick={showFailedPreviews}>Show them</button>
          </div>
        )}

        <div className="attention-block">
          <p>Inbox imports that failed</p>
          {isLoading && <p className="hint">Loading…</p>}
          {jobs && jobs.length === 0 && <p className="hint">None.</p>}
          {jobs?.map((job) => (
            <div key={job.id} className="job-row">
              <div className="job-text">
                <div className="job-name">{job.filename ?? `job ${job.id}`}</div>
                <div className="job-error">{job.last_error}</div>
              </div>
              <button
                disabled={busy === job.id}
                onClick={() => act(job, () => api.retryJob(job.id), () => `Retrying ${job.filename ?? 'the import'}.`)}
              >
                Retry
              </button>
              <button
                disabled={busy === job.id}
                onClick={() =>
                  act(
                    job,
                    () => api.dismissJob(job.id),
                    (r) => {
                      const left = (r as { file_left_at: string | null }).file_left_at
                      return left ? `Dismissed. The file is still at ${left}.` : 'Dismissed.'
                    },
                  )
                }
              >
                Dismiss
              </button>
            </div>
          ))}
        </div>
        {note && <p className="hint">{note}</p>}
      </div>
    </div>
  )
}
