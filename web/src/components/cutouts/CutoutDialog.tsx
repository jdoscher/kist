// F7: "Cut out subject": choose fast or fine edges, feather and shadow,
// then follow the job(s) to the new transparent item. Opens from the
// preview, the info panel, the phone item sheet, the selection bar and a
// board (cutouts.ts openCutout); CutoutHost shows it.

import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Scissors } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api, newBatchId } from '../../api'
import {
  closeCutout,
  cutoutApi,
  DEFAULT_OPTIONS,
  downloadNotice,
  isFinished,
  jobText,
  modelChoices,
  optionErrors,
  type CutoutJob,
  type CutoutOptions,
  type CutoutRequest,
} from '../../cutouts'
import { useAfterUndo, useRefresh } from '../../data'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { Modal } from '../Dialog'
import { useNotices } from '../Notices'

export default function CutoutDialog({ req }: { req: CutoutRequest }) {
  const uid = useId()
  const { items, board } = req
  const one = items.length === 1 ? items[0] : null
  const status = useQuery({ queryKey: ['cutout-status'], queryFn: cutoutApi.status, refetchInterval: (q) => (q.state.data?.models.some((m) => m.state === 'downloading') ? 3000 : false) })
  const [opts, setOpts] = useState<CutoutOptions>(DEFAULT_OPTIONS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [jobs, setJobs] = useState<CutoutJob[] | null>(null)
  const [skipped, setSkipped] = useState<{ item_id: string; reason: string }[]>([])
  const batch = useRef(newBatchId())
  const refresh = useRefresh()
  const afterUndo = useAfterUndo()
  const qc = useQueryClient()
  const { notify } = useNotices()
  const { setPreviewId, selectOnly } = useAppState()

  const st = status.data
  const choices = modelChoices(st)
  const model = opts.model ?? st?.default_model ?? null
  const notice = downloadNotice(st, model)
  const tooBig = choices.find((m) => m.name === model)?.memory_problem ?? null
  const errors = [...optionErrors(opts, st?.feather_max), ...(tooBig ? [`This model ${tooBig}.`] : [])]

  const polled = useQueries({
    queries: (jobs ?? []).map((j) => ({
      queryKey: ['cutout-job', j.id],
      queryFn: () => cutoutApi.job(j.id),
      refetchInterval: (q: { state: { data?: CutoutJob } }) => (isFinished(q.state.data) ? false : 1500),
    })),
  })
  const live = polled.map((q, i) => q.data ?? jobs![i])
  const allDone = jobs !== null && live.length > 0 && live.every((j) => isFinished(j))
  const results = live.filter((j) => j.status === 'done' && j.result_item_id).map((j) => j.result_item_id!)
  const failed = live.filter((j) => j.status === 'failed')

  // once: the grid shows the new items; a board takes its swapped node
  const reported = useRef(false)
  useEffect(() => {
    if (!allDone || reported.current) return
    reported.current = true
    if (results.length) refresh.itemsAdded()
    void qc.invalidateQueries({ queryKey: ['batches'] })
    if (board && results.length) {
      void qc.invalidateQueries({ queryKey: ['board', board.boardId] })
      // the notice carries Undo; the dialog has nothing left to say
      closeCutout()
      notify({
        message: 'The cut-out is on the board in place of the image.',
        timeoutMs: 15_000,
        action: {
          label: 'Undo',
          run: () => void api.undoBatch(batch.current).then(() => {
            afterUndo()
            void qc.invalidateQueries({ queryKey: ['board', board.boardId] })
          }),
        },
      })
    }
  }, [allDone]) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (errors.length) return
    setBusy(true)
    setError('')
    const o = { ...opts, model }
    try {
      if (board) {
        const r = await cutoutApi.boardNode(board.boardId, board.nodeId, o, batch.current)
        if (r.job) setJobs([r.job])
        else {
          void qc.invalidateQueries({ queryKey: ['board', board.boardId] })
          notify({
            message: 'Used the cut-out you already have.',
            action: { label: 'Undo', run: () => void api.undoBatch(batch.current).then(() => void qc.invalidateQueries({ queryKey: ['board', board.boardId] })) },
          })
          closeCutout()
        }
      } else if (one) {
        setJobs([await cutoutApi.one(one.id, o, batch.current)])
      } else {
        const r = await cutoutApi.many(items.map((i) => i.id), o, batch.current)
        setSkipped(r.skipped)
        setJobs(r.jobs)
      }
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const title = one ? `Cut out “${one.filename}”` : `Cut out ${items.length} images`
  const unavailable = st && !st.available ? st.reason : null

  return (
    <Modal title={title} onClose={closeCutout} className="dialog cutout-dialog">
      {jobs === null ? (
        <form className="dialog-form" onSubmit={submit}>
          <fieldset className="cutout-models">
            <legend className="section-label">Model</legend>
            {(choices.length ? choices : [{ name: 'isnet-general-use', label: 'Fast', note: 'a few seconds a cut-out' }]).map((m) => (
              <label key={m.name} className={`cutout-model${model === m.name ? ' is-on' : ''}${'memory_problem' in m && m.memory_problem ? ' is-off' : ''}`}>
                <input
                  type="radio"
                  name={`${uid}-model`}
                  value={m.name}
                  checked={model === m.name}
                  disabled={'memory_problem' in m && !!m.memory_problem}
                  onChange={() => setOpts({ ...opts, model: m.name })}
                />
                <span>
                  <span className="cutout-model-name">{m.label}</span>
                  <span className="cutout-model-note">
                    {'memory_problem' in m && m.memory_problem ? `Not available: ${m.memory_problem}.` : m.note}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="form-grid">
            <label htmlFor={`${uid}-feather`}>Edge feather</label>
            <span className="cutout-feather">
              <input id={`${uid}-feather`} type="range" min={0} max={st?.feather_max ?? 20} step={0.5} value={opts.feather} onChange={(e) => setOpts({ ...opts, feather: Number(e.target.value) })} aria-valuetext={`${opts.feather} pixels`} />
              <output htmlFor={`${uid}-feather`} className="mono">{opts.feather} px</output>
            </span>
            <span />
            <label className="check-inline">
              <input type="checkbox" checked={opts.shadow} onChange={(e) => setOpts({ ...opts, shadow: e.target.checked })} /> Keep the shadow
            </label>
          </div>
          <p className="hint">
            {board ? 'The cut-out replaces the image on the board; undo puts it back. ' : ''}
            {one ? 'The cut-out becomes a new transparent PNG, linked to this image, with its tags and folders. The original stays as it is.' : 'Each cut-out becomes a new transparent PNG, linked to its image. The originals stay as they are.'}
          </p>
          {notice && (
            <p className="cutout-download" role="note">
              {notice}
            </p>
          )}
          <div className="error" role="alert">
            {unavailable ? `Cut-outs aren't available: ${unavailable}.` : (errors[0] ?? error)}
          </div>
          <div className="dialog-actions">
            <button type="button" onClick={closeCutout}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" data-autofocus disabled={busy || errors.length > 0 || !!unavailable}>
              <Scissors size={14} aria-hidden="true" /> {busy ? 'Starting…' : board ? 'Cut out' : 'Cut out subject'}
            </button>
          </div>
        </form>
      ) : (
        <div className="dialog-form">
          <p role="status" aria-live="polite">
            {jobs.length === 0
              ? 'Nothing to cut out.'
              : jobs.length === 1
                ? jobText(live[0])
                : `${live.filter((j) => isFinished(j)).length} of ${jobs.length} done${failed.length ? `, ${failed.length} failed` : ''}.`}
          </p>
          {!allDone && jobs.length > 0 && <progress aria-label="Cut-out progress" {...(jobs.length > 1 ? { max: jobs.length, value: live.filter((j) => isFinished(j)).length } : {})} />}
          {skipped.length > 0 && (
            <p className="hint">
              Skipped {skipped.length}: {skipped.slice(0, 3).map((s) => s.reason).join('; ')}
              {skipped.length > 3 ? '…' : ''}
            </p>
          )}
          {jobs.length > 1 && failed.length > 0 && <p className="error">{failed[0].last_error}</p>}
          <div className="dialog-actions">
            <button type="button" onClick={closeCutout}>
              Close
            </button>
            {allDone && results.length > 0 && !board && (
              <button
                type="button"
                className="btn-primary"
                data-autofocus
                onClick={() => {
                  closeCutout()
                  selectOnly(results[0])
                  setPreviewId(results[0])
                }}
              >
                {results.length === 1 ? 'Open the cut-out' : 'Open the first'}
              </button>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
