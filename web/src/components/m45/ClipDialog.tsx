// Cut a clip from a video (MP4, GIF, animated WebP) or audio (MP3, WAV):
// in and out from the player or typed, a worker job, then the new item
// (linked derived_from the original).

import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { api45 } from '../../m45/api45'
import { AUDIO_CLIP_FORMATS, clipErrors, fmtTime, parseTime, VIDEO_CLIP_FORMATS, type ClipFormat } from '../../m45/media'
import { useRefresh } from '../../data'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { Modal } from '../Dialog'
import type { ClipRequest } from './M45'

export default function ClipDialog({ req, onClose }: { req: ClipRequest; onClose: () => void }) {
  const { item } = req
  const audio = item.kind === 'audio'
  const formats = audio ? AUDIO_CLIP_FORMATS : VIDEO_CLIP_FORMATS
  const duration = item.duration_s ?? null
  const [startText, setStartText] = useState(fmtTime(req.start))
  const [endText, setEndText] = useState(fmtTime(req.end))
  const [format, setFormat] = useState<ClipFormat>(formats[0][0])
  const [maxSide, setMaxSide] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [jobId, setJobId] = useState<number | null>(null)
  const { setPreviewId, selectOnly } = useAppState()
  const refresh = useRefresh()
  const id = useId()

  const start = parseTime(startText)
  const end = parseTime(endText)
  const errors = start === null || end === null ? ['Times look like 1:02.5 or 62.5 (seconds).'] : clipErrors(start, end, duration, format)

  const job = useQuery({
    queryKey: ['clip-job', jobId],
    queryFn: () => api45.job(jobId!),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data?.status === 'done' || q.state.data?.status === 'failed' ? false : 1000),
  })
  const status = job.data?.status
  const resultId = job.data?.result_item_id ?? null
  useEffect(() => {
    if (status === 'done' && resultId) refresh.itemsAdded()
  }, [status, resultId, refresh])

  const fromPlayer = (which: 'start' | 'end') => {
    const p = req.player?.()
    if (!p) return
    ;(which === 'start' ? setStartText : setEndText)(fmtTime(p.currentTime))
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (errors.length || start === null || end === null) return
    setBusy(true)
    setError('')
    try {
      const side = maxSide ? Number(maxSide) : null
      const j = await api45.clip(item.id, { start, end, format, max_side: !audio && side ? side : null })
      setJobId(j.id)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`Clip from “${item.filename}”`} onClose={onClose} className="dialog clip-dialog">
      {jobId === null ? (
        <form className="dialog-form" onSubmit={submit}>
          <div className="form-grid">
            <label htmlFor={`${id}-in`}>In</label>
            <span className="copy-row">
              <input id={`${id}-in`} data-autofocus className="mono" value={startText} onChange={(e) => setStartText(e.target.value)} aria-describedby={`${id}-err`} />
              {req.player && (
                <button type="button" onClick={() => fromPlayer('start')}>
                  Use current time
                </button>
              )}
            </span>
            <label htmlFor={`${id}-out`}>Out</label>
            <span className="copy-row">
              <input id={`${id}-out`} className="mono" value={endText} onChange={(e) => setEndText(e.target.value)} aria-describedby={`${id}-err`} />
              {req.player && (
                <button type="button" onClick={() => fromPlayer('end')}>
                  Use current time
                </button>
              )}
            </span>
            <label htmlFor={`${id}-fmt`}>Format</label>
            <select id={`${id}-fmt`} value={format} onChange={(e) => setFormat(e.target.value as ClipFormat)}>
              {formats.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
            {!audio && (
              <>
                <label htmlFor={`${id}-side`}>Longest side (px)</label>
                <input id={`${id}-side`} inputMode="numeric" placeholder="as the original" value={maxSide} onChange={(e) => setMaxSide(e.target.value.replace(/\D/g, ''))} />
              </>
            )}
          </div>
          <p className="hint">
            {start !== null && end !== null && end > start ? `${fmtTime(end - start)} long.` : ''}
            {duration ? ` The ${audio ? 'recording' : 'video'} is ${fmtTime(duration)}.` : ''} The clip becomes a new item, linked to this one.
          </p>
          <div id={`${id}-err`} className="error" role="alert">
            {errors[0] ?? error}
          </div>
          <div className="dialog-actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy || errors.length > 0}>
              {busy ? 'Starting…' : 'Make the clip'}
            </button>
          </div>
        </form>
      ) : (
        <div className="dialog-form">
          <p role="status" aria-live="polite">
            {(!status || status === 'pending') && 'Waiting for the worker…'}
            {status === 'running' && 'Cutting the clip…'}
            {status === 'done' && (resultId ? 'The clip is ready.' : 'Done.')}
            {status === 'failed' && `The clip failed: ${job.data?.last_error ?? 'unknown error'}.`}
          </p>
          {(!status || status === 'pending' || status === 'running') && <progress aria-label="Clip progress" />}
          <div className="dialog-actions">
            <button type="button" onClick={onClose}>
              Close
            </button>
            {status === 'done' && resultId && (
              <button
                type="button"
                className="primary"
                data-autofocus
                onClick={() => {
                  onClose()
                  selectOnly(resultId)
                  setPreviewId(resultId)
                }}
              >
                Open the clip
              </button>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
