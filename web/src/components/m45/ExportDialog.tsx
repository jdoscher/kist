// A portable export: a BagIt zip of a folder, smart folder, board, the
// selection or everything, with readable names, sidecars and an offline
// index.html. Runs as a job; this polls it and offers the download.

import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useId, useState } from 'react'
import { fmtBytes } from '../../format'
import { api45, type ExportRecord } from '../../m45/api45'
import { describeError } from '../../writes'
import { Modal } from '../Dialog'
import type { Target } from './M45'
import { ClearanceWarning } from '../credits/Credits' // F4

export default function ExportDialog({ target, onClose }: { target: Target; onClose: () => void }) {
  const [originals, setOriginals] = useState(true)
  const [sidecars, setSidecars] = useState(true)
  const [events, setEvents] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [rec, setRec] = useState<ExportRecord | null>(null)
  const id = useId()

  const poll = useQuery({
    queryKey: ['export', rec?.id],
    queryFn: () => api45.getExport(rec!.id),
    enabled: !!rec,
    refetchInterval: (q) => {
      const s = q.state.data?.status ?? rec?.status
      return s === 'done' || s === 'failed' ? false : 1000
    },
  })
  const cur = poll.data ?? rec

  const start = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const body =
        target.kind === 'items'
          ? { target: 'items', item_ids: target.itemIds }
          : target.kind === 'all'
            ? { target: 'all' }
            : { target: target.kind, target_id: target.id ?? undefined }
      setRec(await api45.createExport({ ...body, include: { originals, sidecars, events }, format: 'bagit-zip' }))
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const what = target.kind === 'all' ? 'the whole library' : target.name ? `“${target.name}”` : 'the selection'
  return (
    <Modal title={`Export ${what}`} onClose={onClose} className="dialog export-dialog">
      {!cur ? (
        <form className="dialog-form" onSubmit={start}>
          <p className="hint">
            A zip you can keep anywhere and open without Kist: files under readable folder names, a spreadsheet of every item
            (items.csv), an index.html that browses offline, and checksums (BagIt 1.0) so you can tell it arrived intact.
          </p>
          <fieldset className="share-fieldset">
            <legend>Include</legend>
            <label className="check-row">
              <input type="checkbox" data-autofocus checked={originals} onChange={(e) => setOriginals(e.target.checked)} />
              Original files
            </label>
            <label className="check-row">
              <input type="checkbox" checked={sidecars} onChange={(e) => setSidecars(e.target.checked)} />
              Sidecars: tags, notes, ratings and provenance as .json and .xmp beside each file
            </label>
            <label className="check-row">
              <input type="checkbox" checked={events} onChange={(e) => setEvents(e.target.checked)} />
              Each item’s history of changes
            </label>
          </fieldset>
          <p className="hint" id={`${id}-keep`}>
            Exports are kept on the server for a few days, then removed.
          </p>
          <ClearanceWarning target={{ kind: target.kind, id: target.id, itemIds: target.itemIds }} />
          <div className="error" role="alert">
            {error}
          </div>
          <div className="dialog-actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={busy || (!originals && !sidecars)}>
              {busy ? 'Starting…' : 'Export'}
            </button>
          </div>
        </form>
      ) : (
        <div className="dialog-form">
          <p role="status" aria-live="polite">
            {cur.status === 'pending' && 'Waiting for the worker…'}
            {cur.status === 'running' && 'Packing the zip…'}
            {cur.status === 'done' &&
              `Ready: ${cur.item_count ?? 0} item${cur.item_count === 1 ? '' : 's'}${cur.size_bytes != null ? `, ${fmtBytes(cur.size_bytes)}` : ''}.`}
            {cur.status === 'failed' && `The export failed: ${cur.error ?? 'unknown error'}.`}
          </p>
          {(cur.status === 'pending' || cur.status === 'running') && <progress aria-label="Export progress" />}
          {poll.isError && <p className="error">{describeError(poll.error)}</p>}
          <div className="dialog-actions">
            <button type="button" onClick={onClose}>
              {cur.status === 'done' ? 'Close' : 'Close (it carries on)'}
            </button>
            {cur.status === 'done' && (
              <a className="button btn-primary btn-icon" data-autofocus href={`/api/exports/${cur.id}/download`} download>
                <Download size={14} aria-hidden="true" /> Download zip
              </a>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
