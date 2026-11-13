// F6: "Set fields…" in the selection bar's More menu: pick a project, fill
// in the fields to change, and they are set on every selected item in the
// project's folders, as one batch (Undo in the notice).

import { useQueryClient } from '@tanstack/react-query'
import { Tags } from 'lucide-react'
import { useId, useState } from 'react'
import { api, newBatchId } from '../../api'
import { useAfterUndo } from '../../data'
import { FIELD_TYPE_LABELS, parseFieldInput, projectsApi, type FieldValue, type ProjectSummary } from '../../projects'
import { describeError } from '../../writes'
import type { BulkMenuItem } from '../BulkBar'
import { Modal } from '../Dialog'
import { useNotices } from '../Notices'
import { useAppState } from '../../state'
import { useProjects } from './hooks'

function BulkFieldsDialog({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const projects = useProjects('all')
  const withFields = (projects.data ?? []).filter((p) => p.fields.length > 0)
  const { query } = useAppState()
  // the grid's project filter picks the project
  const [projectId, setProjectId] = useState(query.project ?? '')
  const project: ProjectSummary | undefined = withFields.find((p) => p.id === projectId) ?? (withFields.length === 1 ? withFields[0] : undefined)
  const [text, setText] = useState<Record<string, string>>({})
  const [clear, setClear] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  const qc = useQueryClient()
  const id = useId()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!project) return
    const values: Record<string, FieldValue> = {}
    for (const f of project.fields) {
      if (clear[f.id]) {
        values[f.id] = null
        continue
      }
      const raw = text[f.id] ?? ''
      if (!raw.trim()) continue
      const parsed = parseFieldInput(f, raw)
      if ('error' in parsed) return setError(parsed.error)
      values[f.id] = parsed.value
    }
    if (!Object.keys(values).length) return setError('Fill in a field to set, or tick Clear.')
    setBusy(true)
    const batch = newBatchId()
    try {
      const r = await projectsApi.bulkFields(ids, values, batch)
      afterUndo()
      void qc.invalidateQueries({ queryKey: ['itemFields'] })
      const skipped = r.skipped.length ? ` ${r.skipped.length} not in ${project.name}’s folders, so left alone.` : ''
      notify({
        key: 'bulk',
        message: `Set fields on ${r.updated} item${r.updated === 1 ? '' : 's'}.${skipped}`,
        action: r.updated ? { label: 'Undo', run: () => void api.undoBatch(r.batch_id).then(afterUndo) } : undefined,
      })
      onClose()
    } catch (err) {
      setError(`Couldn’t set the fields: ${describeError(err)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`Set fields on ${ids.length} items`} onClose={onClose} className="dialog pj-dialog">
      <form className="dialog-form" onSubmit={(e) => void submit(e)}>
        {projects.isLoading && <p role="status">Loading…</p>}
        {!projects.isLoading && withFields.length === 0 && (
          <p className="hint">No project has custom fields yet. Add some on a project’s page (Projects in the sidebar).</p>
        )}
        {withFields.length > 1 && (
          <>
            <label htmlFor={`${id}-p`}>Project</label>
            <select id={`${id}-p`} data-autofocus value={project?.id ?? ''} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">Choose a project…</option>
              {withFields.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </>
        )}
        {project && (
          <>
            <p className="hint">Only items filed in {project.name}’s folders change. Fields left empty stay as they are.</p>
            {project.fields.map((f) => (
              <div key={f.id} className="pj-bulk-row">
                <label htmlFor={`${id}-${f.id}`}>
                  {f.name} <span className="pj-field-type">{FIELD_TYPE_LABELS[f.type]}</span>
                </label>
                <div className="pj-bulk-control">
                  {f.type === 'choice' ? (
                    <select id={`${id}-${f.id}`} disabled={clear[f.id]} value={text[f.id] ?? ''} onChange={(e) => setText({ ...text, [f.id]: e.target.value })}>
                      <option value="">(leave as it is)</option>
                      {(f.choices ?? []).map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={`${id}-${f.id}`}
                      type={f.type === 'date' ? 'date' : f.type === 'url' ? 'url' : 'text'}
                      inputMode={f.type === 'number' ? 'decimal' : undefined}
                      disabled={clear[f.id]}
                      value={text[f.id] ?? ''}
                      placeholder="(leave as it is)"
                      onChange={(e) => setText({ ...text, [f.id]: e.target.value })}
                    />
                  )}
                  <label className="pj-clear">
                    <input type="checkbox" checked={!!clear[f.id]} onChange={(e) => setClear({ ...clear, [f.id]: e.target.checked })} /> Clear
                  </label>
                </div>
              </div>
            ))}
          </>
        )}
        <div className="error" role="alert">
          {error}
        </div>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!project || busy}>
            {busy ? 'Setting…' : 'Set fields'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** The More menu entry and its dialog (render `element` in the bar). */
export function useBulkProjectFields(ids: string[], busy: boolean): { items: BulkMenuItem[]; element: React.ReactNode } {
  const [open, setOpen] = useState(false)
  return {
    items: [
      { key: 'fields', label: 'Set fields…', icon: Tags, disabled: busy, title: 'Custom fields of a project (season, edition…)', run: () => setOpen(true) },
    ],
    element: open ? <BulkFieldsDialog ids={ids} onClose={() => setOpen(false)} /> : null,
  }
}
