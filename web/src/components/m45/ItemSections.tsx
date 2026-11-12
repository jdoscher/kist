// The info panel's Milestone 4/5 sections: provenance (editable), the
// page snapshot, link status, and media details (camera, exposure, date
// taken, place and GPS with a privacy note; duration, frame rate, codec).
// Loaded apart from the first page.

import { useQuery } from '@tanstack/react-query'
import { ExternalLink, FileText, MapPin } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import type { Item } from '../../api'
import { useApplyItems, useCaps } from '../../data'
import { linkableUrl } from '../../format'
import { api45 } from '../../m45/api45'
import { clearedM45, bboxString } from '../../m45/filters'
import { fmtLatLon, mediaGps, mediaRows } from '../../m45/media'
import {
  aiChoice,
  aiValue,
  digitalSourceText,
  LICENSE_SUGGESTIONS,
  METHOD_TEXT,
  PROV_FIELDS,
  provenanceDiff,
  provenanceErrors,
  sourceStatusText,
  type AiChoice,
  type Provenance,
} from '../../m45/provenance'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { useM45 } from './M45'

function SourceBadge({ item }: { item: Item }) {
  const s = item.source_status
  // the link-rot watch is off by default: nothing to say until it checked
  if (!s || (!item.source_url && !item.provenance?.page_url)) return null
  const wayback = linkableUrl(item.source_wayback_url)
  return (
    <div className="prov-status">
      <span className={`status-badge status-${s ?? 'unchecked'}`}>{sourceStatusText(s)}</span>
      {item.source_checked_at && <span className="hint">checked {new Date(item.source_checked_at).toLocaleDateString()}</span>}
      {wayback && (
        <a href={wayback.href} target="_blank" rel="noreferrer noopener">
          Archived copy (Wayback Machine) <ExternalLink size={12} strokeWidth={1.75} aria-hidden="true" />
          <span className="sr-only"> (new tab)</span>
        </a>
      )}
    </div>
  )
}

function ProvenanceSection({ item, editable }: { item: Item; editable: boolean }) {
  const prov = (item.provenance ?? {}) as Provenance
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<Provenance>({})
  const [ai, setAi] = useState<AiChoice>(aiChoice(prov))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const apply = useApplyItems()
  const id = useId()
  const { openSnapshot } = useM45()
  const snapshot = useQuery({
    queryKey: ['snapshot-text', item.id],
    queryFn: () => api45.snapshotText(item.id),
    retry: false,
    staleTime: 60_000,
  })

  const begin = () => {
    setForm(Object.fromEntries(PROV_FIELDS.map((f) => [f.key, String(prov[f.key] ?? '')])))
    setAi(aiChoice(prov))
    setError('')
    setEditing(true)
  }
  const errors = provenanceErrors(form)
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (Object.keys(errors).length) return
    const diff = provenanceDiff(prov, { ...form, ai_generated: aiValue(ai) })
    if (!Object.keys(diff).length) {
      setEditing(false)
      return
    }
    setBusy(true)
    try {
      apply([await api45.patchProvenance(item.id, diff)])
      setEditing(false)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const method = typeof prov.method === 'string' ? (METHOD_TEXT[prov.method] ?? prov.method) : null
  const captured = typeof prov.captured_at === 'string' ? new Date(prov.captured_at).toLocaleDateString() : null
  const dst = digitalSourceText(prov.digital_source_type)
  const fromFile = !!prov.c2pa || !!prov.digital_source_type
  const shown = PROV_FIELDS.filter((f) => prov[f.key])

  return (
    <section className="detail-section prov-section" aria-labelledby={`${id}-h`}>
      <div className="section-head-row">
        <h3 id={`${id}-h`} className="section-label">
          Where it came from
        </h3>
        {editable && !editing && (
          <button type="button" className="small" onClick={begin}>
            Edit
          </button>
        )}
      </div>
      {(method || captured) && (
        <p className="hint">
          {[method, captured].filter(Boolean).join(' · ')}
          {typeof prov.imported_from === 'string' ? ` · from ${prov.imported_from}` : ''}
        </p>
      )}
      <SourceBadge item={item} />
      {!editing ? (
        <>
          <div className="meta-list">
            {shown.map((f) => {
              const v = String(prov[f.key])
              const link = f.kind === 'url' ? linkableUrl(v) : null
              return (
                <div key={f.key}>
                  <span>{f.label}</span>
                  {link ? (
                    <a href={link.href} target="_blank" rel="noreferrer noopener" className="prov-link">
                      {link.host} <ExternalLink size={12} strokeWidth={1.75} aria-hidden="true" />
                      <span className="sr-only"> (new tab)</span>
                    </a>
                  ) : (
                    <span className="source-text">{v}</span>
                  )}
                </div>
              )
            })}
            <div>
              <span>AI-generated</span>
              <span>{aiChoice(prov) === 'yes' ? 'Yes' : aiChoice(prov) === 'no' ? 'No' : 'Unknown'}</span>
            </div>
          </div>
          {dst && <p className="hint">The file says: {dst}{prov.c2pa ? ' (C2PA Content Credentials)' : ' (IPTC)'}.</p>}
          {!shown.length && !editable && <p className="hint">Nothing recorded.</p>}
        </>
      ) : (
        <form className="prov-form" onSubmit={save}>
          {PROV_FIELDS.map((f) => (
            <div key={f.key} className="prov-field">
              <label htmlFor={`${id}-${f.key}`}>{f.label}</label>
              <input
                id={`${id}-${f.key}`}
                type={f.kind === 'url' ? 'url' : 'text'}
                value={String(form[f.key] ?? '')}
                list={f.key === 'license' ? `${id}-lic` : undefined}
                onChange={(e) => setForm((p) => ({ ...p, [f.key]: e.target.value }))}
                aria-invalid={!!errors[f.key]}
                aria-describedby={errors[f.key] ? `${id}-${f.key}-err` : undefined}
                maxLength={2000}
              />
              {errors[f.key] && (
                <span id={`${id}-${f.key}-err`} className="error">
                  {errors[f.key]}
                </span>
              )}
            </div>
          ))}
          <datalist id={`${id}-lic`}>
            {LICENSE_SUGGESTIONS.map((l) => (
              <option key={l} value={l} />
            ))}
          </datalist>
          <fieldset className="prov-ai">
            <legend>AI-generated</legend>
            {(['unknown', 'yes', 'no'] as AiChoice[]).map((c) => (
              <label key={c} className="check-row">
                <input type="radio" name={`${id}-ai`} value={c} checked={ai === c} onChange={() => setAi(c)} />
                {c === 'unknown' ? 'Unknown' : c === 'yes' ? 'Yes' : 'No'}
              </label>
            ))}
          </fieldset>
          <p className="hint">
            {fromFile
              ? 'Kist read this from the file’s Content Credentials (C2PA) or IPTC data; your choice here overrides it.'
              : 'Kist sets this from Content Credentials (C2PA) or IPTC data when a file carries them.'}
          </p>
          <div className="error" role="alert">
            {error}
          </div>
          <div className="dialog-actions">
            <button type="button" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={busy || Object.keys(errors).length > 0}>
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      )}
      {snapshot.data !== undefined && (
        <button type="button" className="btn-icon small" onClick={() => openSnapshot(item)}>
          <FileText size={14} strokeWidth={1.75} aria-hidden="true" /> Saved page
        </button>
      )}
    </section>
  )
}

function MediaDetails({ item }: { item: Item }) {
  const rows = useMemo(() => mediaRows(item.media ?? null, item.taken_at), [item.media, item.taken_at])
  const gps = mediaGps(item.media ?? null)
  const { setQuery } = useAppState()
  const id = useId()
  if (!rows.length && !gps && !item.place) return null
  const near = () => {
    if (!gps) return
    const d = 0.05
    setQuery({ ...clearedM45(), bbox: bboxString([gps.lon - d, gps.lat - d, gps.lon + d, gps.lat + d], 0) })
  }
  return (
    <section className="detail-section" aria-labelledby={`${id}-h`}>
      <h3 id={`${id}-h`} className="section-label">
        {item.kind === 'video' || item.kind === 'audio' ? 'Media' : item.kind === 'model' ? 'Model' : 'Camera'}
      </h3>
      <div className="meta-list">
        {rows.map(([k, v]) => (
          <div key={k}>
            <span>{k}</span>
            <span>{v}</span>
          </div>
        ))}
        {item.place && (
          <div>
            <span>Place</span>
            <span>{item.place}</span>
          </div>
        )}
        {gps && (
          <div>
            <span>Location</span>
            <span className="mono">{fmtLatLon(gps.lat, gps.lon)}</span>
          </div>
        )}
      </div>
      {gps && (
        <>
          <button type="button" className="btn-icon small" onClick={near}>
            <MapPin size={14} strokeWidth={1.75} aria-hidden="true" /> Items near here
          </button>
          <p className="hint privacy-note">
            The original file still holds this location. Exported copies remove it; a share link that offers originals passes it on.
          </p>
        </>
      )}
    </section>
  )
}

export default function ItemSections({ item }: { item: Item }) {
  const editable = useCaps().canEdit
  return (
    <>
      <MediaDetails item={item} />
      <ProvenanceSection item={item} editable={editable} />
    </>
  )
}
