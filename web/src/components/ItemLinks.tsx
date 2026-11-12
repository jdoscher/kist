import { useQuery } from '@tanstack/react-query'
import { File, Link2, X } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { api, type Item, type ItemLink, type LinkKind } from '../api'
import { useAfterUndo } from '../data'
import { LINK_KINDS, linkLabel } from '../history'
import { useAppState } from '../state'
import { useWrite } from '../useWrite'
import { Modal } from './Dialog'
import { useNotices } from './Notices'

function LinkThumb({ url }: { url: string | null }) {
  return url ? (
    <img className="link-thumb" src={url} alt="" loading="lazy" />
  ) : (
    <span className="link-thumb icon" aria-hidden="true">
      <File size={16} strokeWidth={1.5} />
    </span>
  )
}

/** Pick another item by filename and the kind of link. */
function LinkPicker({ item, onClose }: { item: Item; onClose: () => void }) {
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [target, setTarget] = useState<string | null>(null)
  const [kind, setKind] = useState<LinkKind>('related')
  const w = useWrite()
  const afterUndo = useAfterUndo()
  const { notify } = useNotices()
  const uid = useId()
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 250)
    return () => clearTimeout(t)
  }, [text])
  const { data, isFetching } = useQuery({
    queryKey: ['linkSearch', q],
    queryFn: () => api.searchItems(q),
    enabled: q.length > 0,
  })
  const results = (data?.items ?? []).filter((i) => i.id !== item.id)
  const hint = LINK_KINDS.find((k) => k.value === kind)?.hint

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!target) return
    const name = results.find((r) => r.id === target)?.filename ?? 'the item'
    void w.run('link', () => api.addLink(item.id, target, kind), {
      what: 'add the link',
      onDone: () => {
        afterUndo()
        notify({ message: `Linked to “${name}”.` })
        onClose()
      },
    })
  }

  return (
    <Modal title="Link to another item" onClose={onClose} className="link-picker">
      <form className="dialog-form" onSubmit={submit}>
        <label htmlFor={`${uid}-q`}>Find the item by name</label>
        <input
          id={`${uid}-q`}
          data-autofocus
          value={text}
          placeholder="File name…"
          onChange={(e) => {
            setText(e.target.value)
            setTarget(null)
          }}
        />
        <fieldset className="link-results">
          <legend className="sr-only">Matching items</legend>
          <p className="hint" role="status">
            {isFetching ? 'Searching…' : q && results.length === 0 ? 'Nothing matches that name.' : ''}
          </p>
          {results.map((r) => (
            <label key={r.id} className={`link-result${target === r.id ? ' on' : ''}`}>
              <input type="radio" name={`${uid}-target`} checked={target === r.id} onChange={() => setTarget(r.id)} />
              <LinkThumb url={r.thumb_url} />
              <span className="link-name">{r.filename}</span>
            </label>
          ))}
        </fieldset>
        <label htmlFor={`${uid}-kind`}>This item is…</label>
        <select
          id={`${uid}-kind`}
          value={kind}
          onChange={(e) => setKind(e.target.value as LinkKind)}
          aria-describedby={`${uid}-kind-hint`}
        >
          {LINK_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
        <p id={`${uid}-kind-hint`} className="hint">
          {hint}
        </p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!target || w.busy()}>
            Link
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** Linked items (thumb, kind, direction); add and remove when editable. */
export function ItemLinks({ item, editable }: { item: Item; editable: boolean }) {
  const { data } = useQuery({ queryKey: ['links', item.id], queryFn: () => api.links(item.id) })
  const { selectOnly } = useAppState()
  const [picking, setPicking] = useState(false)
  const w = useWrite()
  const afterUndo = useAfterUndo()
  const links = data ?? []
  if (!editable && links.length === 0) return null

  const remove = (l: ItemLink) => {
    // the link belongs to the item it points from
    const [src, dst] = l.direction === 'out' ? [item.id, l.item.id] : [l.item.id, item.id]
    void w.run(`unlink:${l.item.id}:${l.kind}`, () => api.removeLink(src, dst, l.kind), {
      what: 'remove the link',
      onDone: afterUndo,
    })
  }

  // the picker sits outside the section, whose label styles it mustn't get
  return (
    <>
      <section className="detail-section" aria-label="Links">
        <div className="section-head-row">
          <h3 className="section-label">Links</h3>
          {editable && (
            <button className="btn-icon small" onClick={() => setPicking(true)}>
              <Link2 size={13} strokeWidth={1.75} aria-hidden="true" />
              Add link
            </button>
          )}
        </div>
        {links.length === 0 ? (
          <p className="hint">
            No links. Link a crop to its original, alternates, or look-alikes that aren’t duplicates.
          </p>
        ) : (
          <ul className="link-list">
            {links.map((l) => {
              const label = linkLabel(l.kind, l.direction)
              return (
                <li key={`${l.direction}:${l.kind}:${l.item.id}`} className="link-row">
                  <button
                    className="link-open"
                    onClick={() => selectOnly(l.item.id)}
                    title={`Show “${l.item.filename}”`}
                  >
                    <LinkThumb url={l.item.thumb_url} />
                    <span className="link-text">
                      <span className="link-kind">{label}</span>
                      <span className="link-name">
                        {l.item.filename}
                        {l.item.state === 'trashed' ? ' (in Trash)' : ''}
                      </span>
                    </span>
                  </button>
                  {editable && (
                    <button
                      className="icon-btn"
                      aria-label={`Remove link: ${label} ${l.item.filename}`}
                      title="Remove this link"
                      disabled={w.busy(`unlink:${l.item.id}:${l.kind}`)}
                      onClick={() => remove(l)}
                    >
                      <X size={14} strokeWidth={1.75} aria-hidden="true" />
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>
      {picking && <LinkPicker item={item} onClose={() => setPicking(false)} />}
    </>
  )
}
