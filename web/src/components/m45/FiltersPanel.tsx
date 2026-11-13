// The Filters panel (Milestones 4 and 5): date taken, camera, lens, place,
// location, orientation, sound, length; AI-generated, licence and the source
// page's status. Applied together; each shows as a chip in the top bar.

import { useId, useState } from 'react'
import type { Query } from '../../api'
import { cleanFilter, clearedM45, m45Filters, type M45FilterKey } from '../../m45/filters'
import { useAppState } from '../../state'
import { Modal } from '../Dialog'

type Form = Record<M45FilterKey, string>

const day = (v: string | null | undefined) => (v ? v.slice(0, 10) : '')

export default function FiltersPanel({ onClose }: { onClose: () => void }) {
  const { query, setQuery } = useAppState()
  const cur = m45Filters(query)
  const [f, setF] = useState<Form>(() => ({
    taken_after: day(cur.taken_after),
    taken_before: day(cur.taken_before),
    date_after: cur.date_after ?? '',
    date_before: cur.date_before ?? '',
    camera: cur.camera ?? '',
    lens: cur.lens ?? '',
    place: cur.place ?? '',
    has_gps: cur.has_gps ?? '',
    vertical: cur.vertical ?? '',
    has_audio: cur.has_audio ?? '',
    duration_min: cur.duration_min ?? '',
    duration_max: cur.duration_max ?? '',
    bbox: cur.bbox ?? '',
    ai_generated: cur.ai_generated ?? '',
    license: cur.license ?? '',
    source_status: cur.source_status ?? '',
    auto_kind: cur.auto_kind ?? '', // F1: kept (picked in the sidebar's Kinds)
  }))
  const id = useId()
  const set = (k: M45FilterKey, v: string) => setF((x) => ({ ...x, [k]: v }))

  const apply = (e: React.FormEvent) => {
    e.preventDefault()
    const patch: Partial<Query> = { ...clearedM45() }
    for (const [k, raw] of Object.entries(f) as [M45FilterKey, string][]) {
      let v = raw.trim()
      if (k === 'taken_after' && v) v = `${v}T00:00:00Z`
      if (k === 'taken_before' && v) v = `${v}T23:59:59Z`
      ;(patch as Record<string, unknown>)[k] = cleanFilter(k, v)
    }
    setQuery(patch)
    onClose()
  }

  const tri = (k: M45FilterKey, label: string, yes = 'Yes', no = 'No') => (
    <>
      <label htmlFor={`${id}-${k}`}>{label}</label>
      <select id={`${id}-${k}`} value={f[k]} onChange={(e) => set(k, e.target.value)}>
        <option value="">Any</option>
        <option value="true">{yes}</option>
        <option value="false">{no}</option>
      </select>
    </>
  )
  const text = (k: M45FilterKey, label: string, placeholder = '') => (
    <>
      <label htmlFor={`${id}-${k}`}>{label}</label>
      <input id={`${id}-${k}`} value={f[k]} onChange={(e) => set(k, e.target.value)} placeholder={placeholder} maxLength={200} />
    </>
  )

  return (
    <Modal title="Filters" onClose={onClose} className="dialog filters-dialog">
      <form className="dialog-form" onSubmit={apply}>
        <fieldset className="share-fieldset">
          <legend>Photos</legend>
          <div className="form-grid">
            <label htmlFor={`${id}-taken_after`}>Taken from</label>
            <input id={`${id}-taken_after`} data-autofocus type="date" value={f.taken_after} onChange={(e) => set('taken_after', e.target.value)} />
            <label htmlFor={`${id}-taken_before`}>Taken until</label>
            <input id={`${id}-taken_before`} type="date" value={f.taken_before} onChange={(e) => set('taken_before', e.target.value)} />
            {text('camera', 'Camera', 'e.g. X100')}
            {text('lens', 'Lens', 'e.g. 35mm')}
            {text('place', 'Place', 'e.g. Kyoto')}
            {tri('has_gps', 'Location', 'Has a location', 'No location')}
          </div>
        </fieldset>
        <fieldset className="share-fieldset">
          <legend>Video and sound</legend>
          <div className="form-grid">
            {tri('vertical', 'Orientation', 'Vertical', 'Not vertical')}
            {tri('has_audio', 'Sound', 'Has sound', 'Silent')}
            <label htmlFor={`${id}-duration_min`}>At least (seconds)</label>
            <input id={`${id}-duration_min`} inputMode="decimal" value={f.duration_min} onChange={(e) => set('duration_min', e.target.value)} />
            <label htmlFor={`${id}-duration_max`}>At most (seconds)</label>
            <input id={`${id}-duration_max`} inputMode="decimal" value={f.duration_max} onChange={(e) => set('duration_max', e.target.value)} />
          </div>
        </fieldset>
        <fieldset className="share-fieldset">
          <legend>Where it came from</legend>
          <div className="form-grid">
            <label htmlFor={`${id}-ai`}>AI-generated</label>
            <select id={`${id}-ai`} value={f.ai_generated} onChange={(e) => set('ai_generated', e.target.value)}>
              <option value="">Any</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
              <option value="unknown">Unknown</option>
            </select>
            {text('license', 'Licence', 'exact, or “none”')}
            <label htmlFor={`${id}-src`}>Source page</label>
            <select id={`${id}-src`} value={f.source_status} onChange={(e) => set('source_status', e.target.value)}>
              <option value="">Any</option>
              <option value="ok">Online</option>
              <option value="gone">Gone</option>
              <option value="moved">Moved</option>
              <option value="error">Couldn’t check</option>
              <option value="unchecked">Not checked yet</option>
            </select>
          </div>
        </fieldset>
        {(f.bbox || f.date_after || f.date_before) && (
          <p className="hint">The map area and timeline dates set elsewhere stay on until you clear them here or with their chips.</p>
        )}
        <div className="dialog-actions">
          <button
            type="button"
            onClick={() => {
              setQuery(clearedM45())
              onClose()
            }}
          >
            Clear all
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary">
            Apply
          </button>
        </div>
      </form>
    </Modal>
  )
}
