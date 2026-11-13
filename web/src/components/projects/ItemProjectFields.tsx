// F6: the info panel's custom fields: one block per project whose folders
// hold the item, each field edited in place (saved on blur / change, one
// undoable event per save). Read-only for read tokens and on the phone
// sheet. Hidden for folder-limited readers (projects aren't theirs).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, ListFilter } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { emptyQuery, type Item } from '../../api'
import { useApplyItems, useCaps } from '../../data'
import {
  fieldValueText,
  filterForValue,
  parseFieldInput,
  projectsApi,
  type FieldDef,
  type FieldValue,
  type ItemProjectFields,
} from '../../projects'
import { clearedM45 } from '../../m45/filters'
import { useAppState } from '../../state'
import { useWrite } from '../../useWrite'

export function ItemProjectFields({ item, editable = true }: { item: Item; editable?: boolean }) {
  const caps = useCaps()
  const enabled = caps.ready && !caps.restricted && item.folder_ids.length > 0
  const q = useQuery({
    // the item's folders and values are in the key: filing it elsewhere
    // or an undo refetches
    queryKey: ['itemFields', item.id, item.folder_ids.join(','), JSON.stringify(item.custom_fields ?? {})],
    queryFn: () => projectsApi.itemFields(item.id),
    enabled,
    staleTime: 30_000,
  })
  if (!enabled || !q.data?.length) return null
  const canEdit = editable && caps.canEdit && item.state === 'active'
  return (
    <>
      {q.data.map((block) => (
        <ProjectBlock key={block.project.id} item={item} block={block} canEdit={canEdit} />
      ))}
    </>
  )
}

function ProjectBlock({ item, block, canEdit }: { item: Item; block: ItemProjectFields; canEdit: boolean }) {
  const { openScreen } = useAppState()
  const id = useId()
  if (!block.fields.length) return null
  return (
    <section className="detail-section pj-item-fields" aria-labelledby={`${id}-h`}>
      <div className="pj-item-head">
        <h3 id={`${id}-h`} className="section-label">
          {block.project.name}
        </h3>
        <button className="link-btn" onClick={() => openScreen({ kind: 'project', id: block.project.id })} title="Open the project">
          Project
        </button>
      </div>
      <dl className="pj-item-list">
        {block.fields.map((f) => (
          <FieldRow key={f.id} item={item} field={f} value={f.value} canEdit={canEdit} />
        ))}
      </dl>
    </section>
  )
}

function FieldRow({ item, field, value, canEdit }: { item: Item; field: FieldDef; value: FieldValue; canEdit: boolean }) {
  const id = useId()
  const { setQuery } = useAppState()
  const qc = useQueryClient()
  const apply = useApplyItems()
  const w = useWrite()
  const [text, setText] = useState(fieldValueText(value))
  const [error, setError] = useState('')
  useEffect(() => {
    setText(fieldValueText(value))
    setError('')
  }, [value])

  const save = (raw: string) => {
    const parsed = parseFieldInput(field, raw)
    if ('error' in parsed) {
      setError(parsed.error)
      return
    }
    setError('')
    if (parsed.value === value || (parsed.value === null && (value === null || value === undefined))) return
    void w.run(`field:${field.id}`, () => projectsApi.setItemFields(item.id, { [field.id]: parsed.value }), {
      what: `save ${field.name}`,
      serializeKey: `item:${item.id}`,
      onDone: (res) => {
        apply([res.item])
        qc.setQueryData(['itemFields', res.item.id, res.item.folder_ids.join(','), JSON.stringify(res.item.custom_fields ?? {})], res.projects)
        void qc.invalidateQueries({ queryKey: ['history'] })
        void qc.invalidateQueries({ queryKey: ['batches'] })
      },
    })
  }
  // every item with this value, library-wide (Back returns here)
  const filter = () => setQuery({ ...emptyQuery, ...clearedM45(), custom_fields: { [field.key]: filterForValue(field, value) } })

  const shown = fieldValueText(value)
  let control
  if (!canEdit) {
    control =
      field.type === 'url' && shown ? (
        <a href={shown} target="_blank" rel="noreferrer noopener" className="pj-item-link">
          {shown} <ExternalLink size={14} aria-hidden="true" />
        </a>
      ) : (
        <span className={shown ? '' : 'pj-item-empty'}>{shown || '—'}</span>
      )
  } else if (field.type === 'choice') {
    const known = (field.choices ?? []).includes(shown)
    control = (
      <select id={`${id}-v`} value={shown} onChange={(e) => save(e.target.value)}>
        <option value="">—</option>
        {!known && shown && <option value={shown}>{shown}</option>}
        {(field.choices ?? []).map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    )
  } else {
    control = (
      <input
        id={`${id}-v`}
        type={field.type === 'number' ? 'text' : field.type === 'date' ? 'date' : field.type === 'url' ? 'url' : 'text'}
        inputMode={field.type === 'number' ? 'decimal' : undefined}
        value={text}
        placeholder={field.type === 'url' ? 'https://…' : field.type === 'number' ? '0' : ''}
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-err` : undefined}
        onChange={(e) => {
          setText(e.target.value)
          if (field.type === 'date') save(e.target.value)
        }}
        onBlur={() => field.type !== 'date' && save(text)}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
    )
  }
  return (
    <div className="pj-item-row">
      <dt>
        <label htmlFor={canEdit ? `${id}-v` : undefined}>{field.name}</label>
      </dt>
      <dd>
        <div className="pj-item-control">
          {control}
          {shown && (
            <button className="icon-btn" onClick={filter} aria-label={`Show items with ${field.name} ${shown}`} title={`Show every item with ${field.name}: ${shown}`}>
              <ListFilter size={14} aria-hidden="true" />
            </button>
          )}
        </div>
        {error && (
          <span id={`${id}-err`} className="error pj-item-error" role="alert">
            {error}
          </span>
        )}
      </dd>
    </div>
  )
}
