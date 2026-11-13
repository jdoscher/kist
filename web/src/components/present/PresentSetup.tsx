// Present (Kist 2.0 F2): the dialog before a show starts. What it will
// show (slides, low-res images), how (surround, captions, filenames, Ken
// Burns, compare pairs, timed advance, gesture timer), offline, the PDF,
// and saved shows (open, save, delete).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useMemo, useState } from 'react'
import { saveBlob } from '../../m45/api45'
import {
  ADVANCE_CHOICES,
  formatBytes,
  GESTURE_CHOICES,
  gestureLabel,
  initialOptions,
  OFFLINE_CAP_CHOICES,
  PRESENT_PREFS_KEY,
  storedOptions,
  type PairMode,
  type PresentRequest,
  type ShowOptions,
} from '../../present'
import { presentApi, type PdfOptions } from '../../presentApi'
import { offlineAvailable } from '../../presentOffline'
import { useCaps } from '../../data'
import { describeError } from '../../writes'
import { Modal, useDialog } from '../Dialog'
import { useNotices } from '../Notices'
import { usePresent } from './PresentHost'

function readStored(): string | null {
  try {
    return localStorage.getItem(PRESENT_PREFS_KEY)
  } catch {
    return null
  }
}

export default function PresentSetup({ request, onClose }: { request: PresentRequest; onClose: () => void }) {
  const [req, setReq] = useState(request)
  const [opts, setOpts] = useState<ShowOptions>(() => initialOptions(readStored()))
  const [offline, setOffline] = useState(false)
  const [capMb, setCapMb] = useState<number>(1000)
  const [pdfOpen, setPdfOpen] = useState(false)
  const [pdf, setPdf] = useState<PdfOptions>({ layout: 'slides', page: 'a4', notes: false, captions: true, filenames: false })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const { startShow } = usePresent()
  const caps = useCaps()
  const qc = useQueryClient()
  const dialog = useDialog()
  const { notify } = useNotices()
  const id = useId()
  const set = (p: Partial<ShowOptions>) => setOpts((o) => ({ ...o, ...p }))

  const saved = useQuery({ queryKey: ['present-shows'], queryFn: presentApi.shows })
  const showKey = req.showId ?? null
  const resolved = useQuery({
    queryKey: ['present-slides', showKey, req.source, req.params, showKey ? null : opts.pairs],
    queryFn: () => (showKey ? presentApi.showSlides(showKey) : presentApi.slides(req.source, { params: req.params, pairs: opts.pairs })),
    staleTime: 0,
    gcTime: 0,
  })
  const data = resolved.data
  // a saved show brings its own options
  useEffect(() => {
    if (showKey && data?.options) setOpts(data.options)
  }, [showKey, data?.options])

  const slideCount = data?.slides.length ?? 0
  const canOffline = offlineAvailable()
  const offlineSize = data ? Math.min(data.offline_bytes, capMb * 1e6) : 0
  // a search has no name of its own: the view's (folder, smart folder, query)
  const name = (showKey ? data?.name : null) ?? (req.source.kind === 'search' ? req.name : (data?.name ?? req.name))

  const start = () => {
    if (!data || !slideCount) return
    try {
      localStorage.setItem(PRESENT_PREFS_KEY, storedOptions(opts))
    } catch {
      /* private mode */
    }
    startShow({ resolved: { ...data, name }, options: opts, offlineCap: offline && canOffline ? capMb * 1e6 : 0, request: req })
  }

  const makePdf = async () => {
    if (!data) return
    setBusy('pdf')
    setError('')
    try {
      const body = showKey ? { source: data.source, show_id: showKey } : { source: req.source, pairs: opts.pairs }
      const { blob, name: file } = await presentApi.pdf(body, pdf, req.params)
      saveBlob(blob, file)
      notify({ message: `Saved ${file}.` })
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy('')
    }
  }

  const saveShow = async () => {
    if (!data) return
    if (showKey) {
      setBusy('save')
      try {
        await presentApi.patchShow(showKey, { options: opts })
        await qc.invalidateQueries({ queryKey: ['present-shows'] })
        notify({ message: `Updated the show “${name}”.` })
      } catch (e) {
        setError(describeError(e))
      } finally {
        setBusy('')
      }
      return
    }
    const title = await dialog.prompt({ title: 'Save this show', label: 'Show name', initial: name, confirmLabel: 'Save' })
    if (!title?.trim()) return
    setBusy('save')
    try {
      const source = req.saveAs ?? req.source
      const ids = data.slides.flatMap((s) => s.items.map((it) => it.id))
      const show = await presentApi.createShow({
        name: title.trim(),
        source,
        options: opts,
        compare: [],
        item_ids: source.kind === 'search' ? [...new Set(ids)] : undefined,
      })
      await qc.invalidateQueries({ queryKey: ['present-shows'] })
      setReq({ name: show.name, source: show.source, showId: show.id })
      notify({ message: `Saved the show “${show.name}”.` })
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy('')
    }
  }

  const deleteShow = async (sid: string, sname: string) => {
    const ok = await dialog.confirm({ title: `Delete the show “${sname}”?`, confirmLabel: 'Delete', danger: true, message: 'Its items, folders and boards stay as they are.' })
    if (!ok) return
    try {
      await presentApi.deleteShow(sid)
      await qc.invalidateQueries({ queryKey: ['present-shows'] })
      // the open show is gone: back to what the dialog was opened for, or close
      if (sid === showKey) {
        if (request.showId === sid) onClose()
        else setReq(request)
      }
    } catch (e) {
      setError(describeError(e))
    }
  }

  const lowRes = data?.low_res ?? 0
  const summary = useMemo(() => {
    if (resolved.isPending) return 'Finding the slides…'
    if (resolved.isError) return ''
    if (!slideCount) return 'Nothing to show here.'
    const compare = data?.slides.filter((s) => s.kind === 'compare').length ?? 0
    return `${slideCount} slide${slideCount === 1 ? '' : 's'}${compare ? `, ${compare} side by side` : ''}${data?.truncated ? ' (the first ones only)' : ''}`
  }, [resolved.isPending, resolved.isError, slideCount, data])

  return (
    <Modal title={`Present “${name}”`} onClose={onClose} className="dialog present-setup">
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          start()
        }}
      >
        <p className="hint present-summary" role="status">
          {summary}
        </p>
        {resolved.isError && (
          <p className="error" role="alert">
            {describeError(resolved.error)}
          </p>
        )}
        {lowRes > 0 && (
          <p className="hint present-lowres">
            {lowRes} image{lowRes === 1 ? ' is' : 's are'} under 1,500 px on the long side and may look soft on a big screen.
          </p>
        )}

        <fieldset className="present-fieldset">
          <legend className="section-label">Look</legend>
          <div className="present-seg" role="radiogroup" aria-label="Surround">
            {(['grey', 'black'] as const).map((s) => (
              <button key={s} type="button" role="radio" aria-checked={opts.surround === s} className={opts.surround === s ? 'on' : ''} onClick={() => set({ surround: s })}>
                <span className={`present-swatch present-swatch-${s}`} aria-hidden="true" />
                {s === 'grey' ? 'Grey surround' : 'Black surround'}
              </button>
            ))}
          </div>
          <label className="check">
            <input type="checkbox" checked={opts.captions} onChange={(e) => set({ captions: e.target.checked })} /> Captions: title and credit
          </label>
          <label className="check">
            <input type="checkbox" checked={opts.filenames} onChange={(e) => set({ filenames: e.target.checked })} /> Show file names
          </label>
          <label className="check">
            <input type="checkbox" checked={opts.ken_burns} onChange={(e) => set({ ken_burns: e.target.checked })} /> Slow pan and zoom (Ken Burns)
          </label>
          {opts.ken_burns && <p className="hint">Never on drawings, documents, screenshots or side-by-side slides. Images stay whole when it’s off.</p>}
        </fieldset>

        <fieldset className="present-fieldset">
          <legend className="section-label">Slides</legend>
          <div className="form-grid">
            <label htmlFor={`${id}-pairs`}>Side by side</label>
            <select id={`${id}-pairs`} value={opts.pairs} disabled={!!showKey} onChange={(e) => set({ pairs: e.target.value as PairMode })}>
              <option value="none">Only pairs I make (J in the show)</option>
              <option value="stacks">Each item with its stack partner</option>
              <option value="versions">Each item with its previous version</option>
            </select>
            <label htmlFor={`${id}-adv`}>Timed advance</label>
            <select
              id={`${id}-adv`}
              value={opts.advance_seconds}
              disabled={opts.gesture_seconds !== null}
              onChange={(e) => set({ advance_seconds: Number(e.target.value) })}
            >
              {ADVANCE_CHOICES.map((s) => (
                <option key={s} value={s}>
                  {s ? `Every ${s} s` : 'Off: I move on'}
                </option>
              ))}
            </select>
            <label htmlFor={`${id}-gest`}>Gesture timer</label>
            <select id={`${id}-gest`} value={opts.gesture_seconds ?? ''} onChange={(e) => set({ gesture_seconds: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Off</option>
              {GESTURE_CHOICES.map((s) => (
                <option key={s} value={s}>
                  {gestureLabel(s)} a pose
                </option>
              ))}
            </select>
          </div>
        </fieldset>

        <fieldset className="present-fieldset">
          <legend className="section-label">Offline</legend>
          <label className="check">
            <input type="checkbox" checked={offline && canOffline} disabled={!canOffline} onChange={(e) => setOffline(e.target.checked)} /> Keep this show offline
          </label>
          {canOffline ? (
            <div className="form-grid">
              <label htmlFor={`${id}-cap`}>Up to</label>
              <select id={`${id}-cap`} value={capMb} onChange={(e) => setCapMb(Number(e.target.value))} disabled={!offline}>
                {OFFLINE_CAP_CHOICES.map((mb) => (
                  <option key={mb} value={mb}>
                    {formatBytes(mb * 1e6)}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <p className="hint">Offline shows need Kist’s app shell (https or localhost, after one visit).</p>
          )}
          {offline && canOffline && data && (
            <p className="hint">
              About {formatBytes(offlineSize)} on this device. The show keeps working without a connection once it’s saved; it replaces the last offline show.
            </p>
          )}
        </fieldset>

        {pdfOpen && (
          <fieldset className="present-fieldset">
            <legend className="section-label">PDF</legend>
            <div className="form-grid">
              <label htmlFor={`${id}-lay`}>Layout</label>
              <select id={`${id}-lay`} value={pdf.layout} onChange={(e) => setPdf({ ...pdf, layout: e.target.value as PdfOptions['layout'] })}>
                <option value="slides">A page per slide, with captions</option>
                <option value="sheet">Contact sheet</option>
              </select>
              <label htmlFor={`${id}-page`}>Page</label>
              <select id={`${id}-page`} value={pdf.page} disabled={pdf.layout === 'sheet'} onChange={(e) => setPdf({ ...pdf, page: e.target.value as PdfOptions['page'] })}>
                <option value="a4">A4 landscape</option>
                <option value="a3">A3 landscape</option>
                <option value="letter">Letter landscape</option>
                <option value="tabloid">11×17 landscape</option>
              </select>
            </div>
            <label className="check">
              <input type="checkbox" checked={pdf.notes} disabled={pdf.layout === 'sheet'} onChange={(e) => setPdf({ ...pdf, notes: e.target.checked })} /> Presenter notes under each slide
            </label>
            <label className="check">
              <input type="checkbox" checked={pdf.filenames} onChange={(e) => setPdf({ ...pdf, filenames: e.target.checked })} /> File names
            </label>
            <button type="button" onClick={() => void makePdf()} disabled={!slideCount || busy !== ''}>
              {busy === 'pdf' ? 'Making the PDF…' : 'Make the PDF'}
            </button>
          </fieldset>
        )}

        {(saved.data?.length ?? 0) > 0 && (
          <details className="present-saved">
            <summary>Saved shows ({saved.data?.length})</summary>
            <ul>
              {saved.data?.map((s) => (
                <li key={s.id} className={s.id === showKey ? 'on' : ''}>
                  <button type="button" className="present-saved-open" onClick={() => setReq({ name: s.name, source: s.source, showId: s.id })} aria-current={s.id === showKey}>
                    {s.name}
                  </button>
                  {caps.canEdit && (
                    <button type="button" className="present-saved-del" aria-label={`Delete the show ${s.name}`} title="Delete this saved show" onClick={() => void deleteShow(s.id, s.name)}>
                      Delete
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}

        <div className="error" role="alert">
          {error}
        </div>
        <div className="dialog-actions">
          <button type="button" onClick={() => setPdfOpen((o) => !o)} aria-expanded={pdfOpen}>
            PDF…
          </button>
          {caps.canEdit && (
            <button type="button" onClick={() => void saveShow()} disabled={!data || busy !== ''}>
              {showKey ? 'Save options' : 'Save show…'}
            </button>
          )}
          <span className="grow" />
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" data-autofocus disabled={!slideCount}>
            Start
          </button>
        </div>
      </form>
    </Modal>
  )
}
