// Export images: resized/converted copies (a zip) or a contact sheet (a
// PDF) of the selection, from a saved preset or one-off settings. GPS is
// always removed. Presets can be saved, changed and deleted here.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { api45, saveBlob } from '../../m45/api45'
import { formToPreset, presetErrors, presetSummary, presetToForm, WATERMARK_POSITIONS, type ImageFormat, type PresetForm } from '../../m45/presets'
import { describeError } from '../../writes'
import { Modal, useDialog } from '../Dialog'
import { useNotices } from '../Notices'

export default function ExportImagesDialog({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const presets = useQuery({ queryKey: ['export-presets'], queryFn: api45.presets })
  const [chosen, setChosen] = useState('web-2048')
  const [form, setForm] = useState<PresetForm>(() => presetToForm({ name: 'web-2048', max_side: 2048, format: 'jpg', quality: 85, strip_metadata: true }))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const qc = useQueryClient()
  const dialog = useDialog()
  const { notify } = useNotices()
  const id = useId()
  const set = (patch: Partial<PresetForm>) => setForm((f) => ({ ...f, ...patch }))
  const errors = presetErrors(form)

  // picking a preset fills the form with it
  useEffect(() => {
    const p = presets.data?.find((x) => x.name === chosen)
    if (p) setForm(presetToForm(p))
  }, [chosen, presets.data])

  const run = async () => {
    if (errors.length) return
    setBusy(true)
    setError('')
    try {
      const { blob, name } = await api45.exportImages(ids, formToPreset(form))
      saveBlob(blob, name)
      notify({ message: `Exported ${ids.length} item${ids.length === 1 ? '' : 's'} as ${name}.` })
      onClose()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  const savePreset = async () => {
    const name = await dialog.prompt({ title: 'Save as a preset', label: 'Preset name', initial: form.name || 'my-preset', confirmLabel: 'Save' })
    if (!name?.trim()) return
    try {
      await api45.savePreset({ ...formToPreset(form), name: name.trim() } as never)
      await qc.invalidateQueries({ queryKey: ['export-presets'] })
      setChosen(name.trim())
      notify({ message: `Saved the preset “${name.trim()}”.` })
    } catch (e) {
      setError(describeError(e))
    }
  }

  const deletePreset = async () => {
    const ok = await dialog.confirm({ title: `Delete the preset “${chosen}”?`, confirmLabel: 'Delete', danger: true, message: 'Exports made with it are not affected.' })
    if (!ok) return
    try {
      await api45.deletePreset(chosen)
      await qc.invalidateQueries({ queryKey: ['export-presets'] })
      setChosen('web-2048')
    } catch (e) {
      setError(describeError(e))
    }
  }

  return (
    <Modal title={`Export ${ids.length} image${ids.length === 1 ? '' : 's'}`} onClose={onClose} className="dialog export-dialog">
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          void run()
        }}
      >
        <label htmlFor={`${id}-preset`}>Preset</label>
        <div className="copy-row">
          <select id={`${id}-preset`} data-autofocus value={chosen} onChange={(e) => setChosen(e.target.value)}>
            {(presets.data ?? []).map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => void savePreset()}>
            Save as…
          </button>
          <button type="button" onClick={() => void deletePreset()} disabled={!presets.data?.some((p) => p.name === chosen)}>
            Delete
          </button>
        </div>
        <p className="hint" role="status">
          {presetSummary(formToPreset(form))}
        </p>

        <div className="form-grid">
          <label htmlFor={`${id}-side`}>Longest side (px)</label>
          <input id={`${id}-side`} inputMode="numeric" placeholder="full size" value={form.maxSide} onChange={(e) => set({ maxSide: e.target.value.trim() })} disabled={form.contactSheet} />
          <label htmlFor={`${id}-fmt`}>Format</label>
          <select id={`${id}-fmt`} value={form.format} onChange={(e) => set({ format: e.target.value as ImageFormat })} disabled={form.contactSheet}>
            <option value="jpg">JPEG</option>
            <option value="webp">WebP</option>
            <option value="png">PNG</option>
            <option value="avif">AVIF</option>
          </select>
          <label htmlFor={`${id}-q`}>Quality</label>
          <span className="range-row">
            <input id={`${id}-q`} type="range" min={1} max={100} value={form.quality} onChange={(e) => set({ quality: Number(e.target.value) })} disabled={form.format === 'png' || form.contactSheet} />
            <output htmlFor={`${id}-q`}>{form.quality}</output>
          </span>
        </div>
        <label className="check-row">
          <input type="checkbox" checked={form.stripMetadata} onChange={(e) => set({ stripMetadata: e.target.checked })} />
          Remove metadata (camera, dates, captions)
        </label>
        <p className="hint">Location (GPS) is always removed from exported copies.</p>

        <fieldset className="share-fieldset">
          <legend>
            <label className="check-row">
              <input type="checkbox" checked={form.watermark} onChange={(e) => set({ watermark: e.target.checked })} />
              Watermark
            </label>
          </legend>
          {form.watermark && (
            <div className="form-grid">
              <label htmlFor={`${id}-wt`}>Text</label>
              <input id={`${id}-wt`} value={form.wmText} maxLength={200} onChange={(e) => set({ wmText: e.target.value })} />
              <label htmlFor={`${id}-wp`}>Position</label>
              <select id={`${id}-wp`} value={form.wmPosition} onChange={(e) => set({ wmPosition: e.target.value })}>
                {WATERMARK_POSITIONS.map((p) => (
                  <option key={p} value={p}>
                    {p.replace('-', ' ')}
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-wo`}>Opacity</label>
              <span className="range-row">
                <input id={`${id}-wo`} type="range" min={0.05} max={1} step={0.05} value={form.wmOpacity} onChange={(e) => set({ wmOpacity: Number(e.target.value) })} />
                <output htmlFor={`${id}-wo`}>{Math.round(form.wmOpacity * 100)}%</output>
              </span>
            </div>
          )}
        </fieldset>

        <fieldset className="share-fieldset">
          <legend>
            <label className="check-row">
              <input type="checkbox" checked={form.contactSheet} onChange={(e) => set({ contactSheet: e.target.checked })} />
              Contact sheet (one PDF)
            </label>
          </legend>
          {form.contactSheet && (
            <div className="form-grid">
              <label htmlFor={`${id}-cols`}>Columns</label>
              <input id={`${id}-cols`} type="number" min={1} max={12} value={form.cols} onChange={(e) => set({ cols: Number(e.target.value) })} />
              <label htmlFor={`${id}-cap`}>Captions</label>
              <select id={`${id}-cap`} value={form.caption} onChange={(e) => set({ caption: e.target.value as 'filename' | 'none' })}>
                <option value="filename">File names</option>
                <option value="none">None</option>
              </select>
            </div>
          )}
        </fieldset>

        <div className="error" role="alert">
          {errors[0] ?? error}
        </div>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy || errors.length > 0}>
            {busy ? 'Exporting…' : form.contactSheet ? 'Make the PDF' : 'Export'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
