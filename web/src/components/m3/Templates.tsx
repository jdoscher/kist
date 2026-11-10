// Metadata templates: named sets of tags, folders, notes, source, rating,
// flag, label, credit and licence. Managed in Settings → Templates and
// applied to a selection from the bulk bar (one batch, with Undo).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useId, useState } from 'react'
import { api } from '../../api'
import { api3, type Template, type TemplateFields } from '../../creativeApi'
import { LABELS } from '../../cull'
import { useAfterUndo, useApplyItems, useFolderNames, useFolders } from '../../data'
import { useAppState } from '../../state'
import { useWrite } from '../../useWrite'
import { describeError } from '../../writes'
import { Modal, useDialog } from '../Dialog'
import { useNotices } from '../Notices'

export function useTemplates(enabled = true) {
  return useQuery({ queryKey: ['templates'], queryFn: api3.templates, enabled, staleTime: 30_000 })
}

/** "adds #a #b, to Shoots, ★★★, pick, credit: X" */
export function templateSummary(f: TemplateFields, folderName: (id: string) => string | undefined): string {
  const parts: string[] = []
  if (f.tags_add?.length) parts.push(`tags ${f.tags_add.map((t) => `#${t}`).join(' ')}`)
  if (f.folder_add?.length) parts.push(`into ${f.folder_add.map((id) => folderName(id) ?? 'a folder').join(', ')}`)
  if (f.rating) parts.push(`${f.rating}★`)
  if (f.flag && f.flag !== 'none') parts.push(f.flag)
  if (f.label && f.label !== 'none') parts.push(`${f.label} label`)
  if (f.credit) parts.push(`credit ${f.credit}`)
  if (f.license) parts.push(`licence ${f.license}`)
  if (f.source_url) parts.push('source URL')
  if (f.notes_append) parts.push('notes')
  return parts.join(' · ') || 'nothing yet'
}

export function TemplatesSection() {
  const templates = useTemplates()
  const qc = useQueryClient()
  const w = useWrite()
  const dialog = useDialog()
  const folderName = useFolderNames()
  const [editing, setEditing] = useState<Template | 'new' | null>(null)
  const refresh = () => void qc.invalidateQueries({ queryKey: ['templates'] })

  const remove = async (t: Template) => {
    const ok = await dialog.confirm({ title: `Delete template “${t.name}”?`, message: 'Items it was applied to keep what it gave them.', confirmLabel: 'Delete', danger: true })
    if (ok) void w.run(`t:${t.id}`, () => api3.deleteTemplate(t.id), { what: 'delete the template', onDone: refresh })
  }

  return (
    <section aria-labelledby="templates-h">
      <div className="settings-section-head">
        <h2 id="templates-h">Templates</h2>
        <button className="primary btn-icon" onClick={() => setEditing('new')}>
          <Plus size={15} aria-hidden="true" /> New template
        </button>
      </div>
      <p className="settings-intro">
        A template applies the same tags, folders, credit, licence, rating, flag or label to many items at once. Select items, then use
        Template in the bar at the bottom. Each use is one batch you can undo.
      </p>
      {templates.isLoading && <p className="hint">Loading…</p>}
      {templates.data?.length === 0 && <p className="hint">No templates yet.</p>}
      <ul className="token-list">
        {(templates.data ?? []).map((t) => (
          <li key={t.id} className="token-card">
            <div className="token-top">
              <h3 className="token-name">{t.name}</h3>
              <span className="grow" />
              <button className="icon-btn" aria-label={`Edit ${t.name}`} title="Edit" onClick={() => setEditing(t)}>
                <Pencil size={14} aria-hidden="true" />
              </button>
              <button className="icon-btn" aria-label={`Delete ${t.name}`} title="Delete" onClick={() => void remove(t)}>
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </div>
            <p className="hint">{templateSummary(t.fields, (id) => folderName.get(id))}</p>
          </li>
        ))}
      </ul>
      {editing && (
        <TemplateDialog
          template={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            refresh()
          }}
        />
      )}
    </section>
  )
}

function TemplateDialog({ template, onClose, onSaved }: { template: Template | null; onClose: () => void; onSaved: () => void }) {
  const f = template?.fields ?? {}
  const { data: folders } = useFolders()
  const [name, setName] = useState(template?.name ?? '')
  const [tags, setTags] = useState((f.tags_add ?? []).join(', '))
  const [folder, setFolder] = useState<string[]>(f.folder_add ?? [])
  const [credit, setCredit] = useState(f.credit ?? '')
  const [license, setLicense] = useState(f.license ?? '')
  const [source, setSource] = useState(f.source_url ?? '')
  const [notes, setNotes] = useState(f.notes_append ?? '')
  const [rating, setRating] = useState(f.rating ?? 0)
  const [flag, setFlag] = useState(f.flag ?? '')
  const [label, setLabel] = useState(f.label ?? '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const id = useId()

  const save = async () => {
    const fields: TemplateFields = {
      tags_add: tags.split(',').map((t) => t.trim()).filter(Boolean),
      folder_add: folder,
      notes_append: notes,
      source_url: source.trim() || null,
      rating: rating || null,
      flag: flag || null,
      label: label || null,
      credit: credit.trim() || null,
      license: license.trim() || null,
    }
    setBusy(true)
    setError('')
    try {
      if (template) await api3.updateTemplate(template.id, name.trim(), fields)
      else await api3.createTemplate(name.trim(), fields)
      onSaved()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={template ? 'Edit template' : 'New template'} onClose={onClose} className="dialog template-dialog">
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) void save()
        }}
      >
        <label htmlFor={`${id}-name`}>Name</label>
        <input id={`${id}-name`} data-autofocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Client shoot: Acme" />
        <label htmlFor={`${id}-tags`}>Add tags (comma-separated)</label>
        <input id={`${id}-tags`} value={tags} onChange={(e) => setTags(e.target.value)} />
        <fieldset className="folder-picks">
          <legend>Add to folders</legend>
          {(folders ?? []).map((fo) => (
            <label key={fo.id} className="check-inline">
              <input
                type="checkbox"
                checked={folder.includes(fo.id)}
                onChange={(e) => setFolder(e.target.checked ? [...folder, fo.id] : folder.filter((x) => x !== fo.id))}
              />
              {fo.name}
            </label>
          ))}
          {!folders?.length && <span className="hint">No folders yet.</span>}
        </fieldset>
        <div className="field-pair">
          <span>
            <label htmlFor={`${id}-credit`}>Credit</label>
            <input id={`${id}-credit`} value={credit} onChange={(e) => setCredit(e.target.value)} placeholder="Photographer, studio" />
          </span>
          <span>
            <label htmlFor={`${id}-lic`}>Licence</label>
            <input id={`${id}-lic`} value={license} onChange={(e) => setLicense(e.target.value)} placeholder="CC BY 4.0, editorial only…" />
          </span>
        </div>
        <p className="hint">Credit and licence become credit: and license: tags, so they can be searched.</p>
        <label htmlFor={`${id}-src`}>Source URL</label>
        <input id={`${id}-src`} type="url" value={source} onChange={(e) => setSource(e.target.value)} placeholder="https://…" />
        <div className="field-pair">
          <span>
            <label htmlFor={`${id}-rating`}>Rating</label>
            <select id={`${id}-rating`} value={rating} onChange={(e) => setRating(Number(e.target.value))}>
              <option value={0}>Leave as is</option>
              {[1, 2, 3, 4, 5].map((r) => (
                <option key={r} value={r}>
                  {r} star{r > 1 ? 's' : ''}
                </option>
              ))}
            </select>
          </span>
          <span>
            <label htmlFor={`${id}-flag`}>Flag</label>
            <select id={`${id}-flag`} value={flag} onChange={(e) => setFlag(e.target.value)}>
              <option value="">Leave as is</option>
              <option value="pick">Pick</option>
              <option value="reject">Reject</option>
              <option value="none">Clear</option>
            </select>
          </span>
          <span>
            <label htmlFor={`${id}-label`}>Label</label>
            <select id={`${id}-label`} value={label} onChange={(e) => setLabel(e.target.value)}>
              <option value="">Leave as is</option>
              {LABELS.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
              <option value="none">Clear</option>
            </select>
          </span>
        </div>
        <label htmlFor={`${id}-notes`}>Append to notes</label>
        <textarea id={`${id}-notes`} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={!name.trim() || busy}>
            {template ? 'Save' : 'Create'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export function ApplyTemplateDialog({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const templates = useTemplates()
  const folderName = useFolderNames()
  const apply = useApplyItems()
  const afterUndo = useAfterUndo()
  const { notify } = useNotices()
  const { openSettings } = useAppState()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  const run = async (t: Template) => {
    setBusy(t.id)
    setError('')
    try {
      const r = await api3.applyTemplate(t.id, ids)
      apply(r.items)
      onClose()
      notify({
        key: 'template',
        message: `Applied “${t.name}” to ${r.items.length} item${r.items.length === 1 ? '' : 's'}.`,
        action: { label: 'Undo', run: () => void api.undoBatch(r.batch_id).then(afterUndo) },
      })
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Modal title={`Apply a template to ${ids.length} item${ids.length === 1 ? '' : 's'}`} onClose={onClose} className="dialog">
      {templates.data?.length === 0 && (
        <p className="dialog-message">
          No templates yet.{' '}
          <button
            className="small"
            onClick={() => {
              onClose()
              openSettings('templates')
            }}
          >
            Make one in Settings
          </button>
        </p>
      )}
      <ul className="template-pick">
        {(templates.data ?? []).map((t, i) => (
          <li key={t.id}>
            <button className="template-option" data-autofocus={i === 0 ? true : undefined} disabled={!!busy} onClick={() => void run(t)}>
              <Sparkles size={15} aria-hidden="true" />
              <span>
                <strong>{t.name}</strong>
                <span className="hint">{templateSummary(t.fields, (id) => folderName.get(id))}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  )
}
