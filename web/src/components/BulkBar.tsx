import { useEffect, useMemo, useState } from 'react'
import type { Item } from '../api'
import { useAppState, useBulkAction, useFolders } from '../state'

export function BulkBar({ items }: { items: Map<string, Item> }) {
  const { query, selection, clearSelection } = useAppState()
  const { data: folders } = useFolders()
  const bulk = useBulkAction()
  const [tagText, setTagText] = useState('')

  const ids = useMemo(() => [...selection], [selection])

  const itemTags = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const id of ids) m.set(id, items.get(id)?.tags ?? [])
    return m
  }, [ids, items])

  // Delete key → trash (or delete forever in trash view)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target.closest('input, textarea, select, [contenteditable]')) return
      if (ids.length === 0) return
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        if (query.deleted) {
          if (confirm(`Permanently delete ${ids.length} item(s)? This cannot be undone.`)) {
            bulk.mutate({ ids, action: { kind: 'hardDelete' } })
            clearSelection()
          }
        } else {
          bulk.mutate({ ids, action: { kind: 'trash' } })
          clearSelection()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ids, query.deleted, bulk, clearSelection])

  if (ids.length === 0) return null

  const addTags = () => {
    const tags = tagText.split(',').map((t) => t.trim()).filter(Boolean)
    if (tags.length) {
      bulk.mutate({ ids, action: { kind: 'addTags', tags, itemTags } })
      setTagText('')
    }
  }

  return (
    <div className="bulkbar">
      <span className="sel-count">{ids.length} selected</span>

      {!query.deleted && (
        <>
          <input
            placeholder="Add tags (comma-sep)"
            value={tagText}
            onChange={(e) => setTagText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addTags()}
          />
          <button onClick={addTags} disabled={!tagText.trim()}>
            Tag
          </button>

          <select
            value=""
            onChange={(e) => {
              if (e.target.value === '') return
              if (e.target.value === '__none__') {
                bulk.mutate({ ids, action: { kind: 'move', folderId: null } })
                return
              }
              const itemFolders = new Map<string, string[]>()
              for (const id of ids) itemFolders.set(id, items.get(id)?.folder_ids ?? [])
              bulk.mutate({
                ids,
                action: { kind: 'addFolder', folderId: e.target.value, itemFolders },
              })
            }}
          >
            <option value="">Add to folder…</option>
            <option value="__none__">(remove from all folders)</option>
            {(folders ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>

          <button
            className="danger"
            onClick={() => {
              bulk.mutate({ ids, action: { kind: 'trash' } })
              clearSelection()
            }}
          >
            Trash
          </button>
        </>
      )}

      {query.deleted && (
        <>
          <button
            onClick={() => {
              bulk.mutate({ ids, action: { kind: 'restore' } })
              clearSelection()
            }}
          >
            Restore
          </button>
          <button
            className="danger"
            onClick={() => {
              if (confirm(`Permanently delete ${ids.length} item(s)? This cannot be undone.`)) {
                bulk.mutate({ ids, action: { kind: 'hardDelete' } })
                clearSelection()
              }
            }}
          >
            Delete forever
          </button>
        </>
      )}

      <button onClick={clearSelection}>✕</button>
      {bulk.isPending && <span className="working">working…</span>}
    </div>
  )
}
