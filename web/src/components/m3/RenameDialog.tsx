// Bulk rename: a pattern with placeholders, a live preview as you type
// (worked out here, then confirmed by the server's dry run), and one
// batch you can undo.

import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useMemo, useState } from 'react'
import type { Item } from '../../api'
import { api3 } from '../../creativeApi'
import { useAfterUndo, useApplyItems, useFolderNames } from '../../data'
import { patternErrors, renameAll, RENAME_TOKENS } from '../../rename'
import { describeError } from '../../writes'
import { api } from '../../api'
import { Modal } from '../Dialog'
import { useNotices } from '../Notices'

export default function RenameDialog({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [pattern, setPattern] = useState('{name}')
  const [start, setStart] = useState(1)
  const [debounced, setDebounced] = useState({ pattern, start })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const id = useId()
  const apply = useApplyItems()
  const afterUndo = useAfterUndo()
  const { notify } = useNotices()
  const folderName = useFolderNames()
  const items = useQuery({ queryKey: ['compare', ids], queryFn: () => api3.itemsByIds(ids) })
  const errors = patternErrors(pattern)

  useEffect(() => {
    const t = setTimeout(() => setDebounced({ pattern, start }), 300)
    return () => clearTimeout(t)
  }, [pattern, start])

  const local = useMemo(() => {
    const list = items.data?.items ?? []
    return renameAll(
      pattern,
      list.map((i: Item) => ({ ...i, folder: i.folder_ids[0] ? (folderName.get(i.folder_ids[0]) ?? '') : '' })),
      start,
    )
  }, [items.data, pattern, start, folderName])

  const dry = useQuery({
    queryKey: ['renameDry', ids, debounced.pattern, debounced.start],
    queryFn: () => api3.rename(ids, debounced.pattern, true, debounced.start),
    enabled: patternErrors(debounced.pattern).length === 0,
    retry: false,
    placeholderData: (p) => p,
  })
  const serverRows = dry.data && debounced.pattern === pattern && debounced.start === start ? dry.data.items : null
  const list = items.data?.items ?? []
  const changed = serverRows ? serverRows.filter((r) => r.changed).length : local.filter((n, i) => n !== list[i]?.filename).length

  const run = async () => {
    setBusy(true)
    setError('')
    try {
      const r = await api3.rename(ids, pattern, false, start)
      const fresh = await api3.itemsByIds(r.items.filter((x) => x.changed).map((x) => x.id))
      apply(fresh.items)
      onClose()
      notify({
        key: 'rename',
        message: `Renamed ${r.items.filter((x) => x.changed).length} of ${r.items.length}.`,
        action: r.batch_id
          ? {
              label: 'Undo',
              run: () => {
                void api.undoBatch(r.batch_id as string).then(afterUndo)
              },
            }
          : undefined,
      })
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`Rename ${ids.length} item${ids.length === 1 ? '' : 's'}`} onClose={onClose} className="dialog rename-dialog">
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (!errors.length && changed) void run()
        }}
      >
        <label htmlFor={`${id}-p`}>Pattern</label>
        <input
          id={`${id}-p`}
          data-autofocus
          className="mono"
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
          aria-invalid={errors.length > 0}
          aria-describedby={`${id}-err ${id}-help`}
          spellCheck={false}
          autoComplete="off"
        />
        <div id={`${id}-err`} className="error" role="alert">
          {errors[0] ?? (dry.isError ? describeError(dry.error) : '')}
        </div>
        <label htmlFor={`${id}-n`} className="inline-field">
          Counter starts at
          <input id={`${id}-n`} type="number" min={0} value={start} onChange={(e) => setStart(Math.max(0, Number(e.target.value) || 0))} />
        </label>
        <details id={`${id}-help`} className="howto" open>
          <summary>Placeholders</summary>
          <ul className="token-help-list">
            {RENAME_TOKENS.map((t) => (
              <li key={t.token}>
                <button type="button" className="small mono" onClick={() => setPattern((p) => p + t.token)} aria-label={`Insert ${t.token}: ${t.help}`}>
                  {t.token}
                </button>
                <span>
                  {t.help} <span className="dim">e.g. {t.example}</span>
                </span>
              </li>
            ))}
          </ul>
        </details>
        <table className="rename-preview" aria-label="Preview of the new names">
          <thead>
            <tr>
              <th scope="col">Now</th>
              <th scope="col">After</th>
            </tr>
          </thead>
          <tbody>
            {list.slice(0, 50).map((it, i) => {
              const after = serverRows?.find((r) => r.id === it.id)?.after ?? local[i]
              return (
                <tr key={it.id} className={after === it.filename ? 'same' : ''}>
                  <td>{it.filename}</td>
                  <td className="mono">{after}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {list.length > 50 && <p className="hint">…and {list.length - 50} more.</p>}
        <p className="hint" role="status">
          {serverRows ? 'Checked with the server.' : 'Preview'} {changed} will change. Renames are one batch: Undo puts every name back.
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy || errors.length > 0 || !changed}>
            {busy ? 'Renaming…' : `Rename ${changed}`}
          </button>
        </div>
      </form>
    </Modal>
  )
}
