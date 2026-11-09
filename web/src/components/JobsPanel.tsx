import { api, emptyQuery, type Job } from '../api'
import { useJobs, useRefresh, useStats } from '../data'
import { useAppState } from '../state'
import { useWrite } from '../useWrite'
import { Modal } from './Dialog'
import { useNotices } from './Notices'

/** Imports that failed (with Retry / Dismiss) and a way to find the items
 * whose preview failed. Opened from "Needs attention" in the sidebar. */
export function JobsPanel() {
  const { jobsOpen, setJobsOpen, setQuery, mobile, closeDrawer } = useAppState()
  const { data: jobs, isLoading } = useJobs(jobsOpen)
  const { data: stats } = useStats()
  const refresh = useRefresh()
  const { notify } = useNotices()
  const w = useWrite()

  if (!jobsOpen) return null

  // a job retry/dismiss changes the job list and the counts, no item
  const act = <T,>(job: Job, verb: string, fn: () => Promise<T>, done: (r: T) => string) =>
    void w.run(`job:${job.id}`, fn, {
      what: `${verb} ${job.filename ?? 'the import'}`,
      onDone: (r) => {
        notify({ message: done(r) })
        refresh.jobs()
      },
    })

  const showFailedPreviews = () => {
    setJobsOpen(false)
    if (mobile) closeDrawer()
    setQuery({ ...emptyQuery, preview: 'failed' })
  }

  const showFixityFailed = () => {
    setJobsOpen(false)
    if (mobile) closeDrawer()
    setQuery({ ...emptyQuery, fixity: 'failed' })
  }
  const fixityFailed = stats?.fixity_failed ?? 0

  return (
    <Modal title="Needs attention" onClose={() => setJobsOpen(false)}>
      {fixityFailed > 0 && (
        <div className="attention-block">
          <p>
            {fixityFailed === 1
              ? '1 original no longer matches its fingerprint: the file on disk changed or is missing. Restore it from a backup.'
              : `${fixityFailed} originals no longer match their fingerprints: the files on disk changed or are missing. Restore them from a backup.`}
          </p>
          <button onClick={showFixityFailed}>Show them</button>
        </div>
      )}
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
        <p className="hint" role="status">
          {isLoading ? 'Loading…' : ''}
        </p>
        {jobs && jobs.length === 0 && <p className="hint">None.</p>}
        {jobs?.map((job) => (
          <div key={job.id} className="job-row">
            <div className="job-text">
              <div className="job-name">{job.filename ?? `job ${job.id}`}</div>
              <div className="job-error">{job.last_error}</div>
            </div>
            <button
              disabled={w.busy(`job:${job.id}`)}
              onClick={() =>
                act(job, 'retry', () => api.retryJob(job.id), () => `Retrying ${job.filename ?? 'the import'}.`)
              }
            >
              Retry
            </button>
            <button
              disabled={w.busy(`job:${job.id}`)}
              onClick={() =>
                act(job, 'dismiss', () => api.dismissJob(job.id), (r) =>
                  r.file_left_at ? `Dismissed. The file is still at ${r.file_left_at}.` : 'Dismissed.',
                )
              }
            >
              Dismiss
            </button>
          </div>
        ))}
      </div>
    </Modal>
  )
}
