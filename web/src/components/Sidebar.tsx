import { useMemo, useState } from 'react'
import { api, emptyQuery, type Folder } from '../api'
import {
  useAppState,
  useBulkAction,
  useFolders,
  useInvalidateItems,
  useItems,
  useSmartFolders,
  useStats,
  useTags,
  flattenItems,
} from '../state'

interface TreeNode extends Folder {
  children: TreeNode[]
}

function buildTree(folders: Folder[]): TreeNode[] {
  const nodes = new Map<string, TreeNode>()
  for (const f of folders) nodes.set(f.id, { ...f, children: [] })
  const roots: TreeNode[] = []
  for (const node of nodes.values()) {
    const parent = node.parent_id ? nodes.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  return roots
}

function fmtBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`
  if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(0)} KB`
  return `${n} B`
}

function FolderRow({
  node,
  depth,
  onDropItems,
}: {
  node: TreeNode
  depth: number
  onDropItems: (folderId: string, ids: string[]) => void
}) {
  const { query, setQuery } = useAppState()
  const invalidate = useInvalidateItems()
  const [expanded, setExpanded] = useState(depth < 1)
  const [dragOver, setDragOver] = useState(false)
  const active = query.folder === node.id && !query.deleted

  const rename = async () => {
    const name = prompt('Rename folder', node.name)
    if (name && name.trim() && name !== node.name) {
      await api.patchFolder(node.id, { name: name.trim() })
      invalidate()
    }
  }

  const remove = async () => {
    if (!confirm(`Delete folder “${node.name}”? Items inside are kept (unfiled).`)) return
    await api.deleteFolder(node.id)
    if (query.folder === node.id) setQuery({ folder: null })
    invalidate()
  }

  const addChild = async () => {
    const name = prompt('New folder name')
    if (name?.trim()) {
      await api.createFolder(name.trim(), node.id)
      setExpanded(true)
      invalidate()
    }
  }

  return (
    <div>
      <div
        className={`row folder-row ${active ? 'active' : ''} ${dragOver ? 'drag-over' : ''}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={() => setQuery({ folder: active ? null : node.id, deleted: false })}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes('application/x-kist-items')) {
            e.preventDefault()
            setDragOver(true)
          }
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          const raw = e.dataTransfer.getData('application/x-kist-items')
          if (raw) onDropItems(node.id, JSON.parse(raw))
        }}
      >
        <button
          className={`twisty ${node.children.length ? '' : 'hidden'} ${expanded ? 'open' : ''}`}
          onClick={(e) => {
            e.stopPropagation()
            setExpanded(!expanded)
          }}
        >
          ▸
        </button>
        <span className="name">{node.name}</span>
        <span className="count">{node.item_count || ''}</span>
        <span className="row-actions">
          <button title="New subfolder" onClick={(e) => { e.stopPropagation(); addChild() }}>+</button>
          <button title="Rename" onClick={(e) => { e.stopPropagation(); rename() }}>✎</button>
          <button title="Delete" onClick={(e) => { e.stopPropagation(); remove() }}>🗑</button>
        </span>
      </div>
      {expanded &&
        node.children.map((c) => (
          <FolderRow key={c.id} node={c} depth={depth + 1} onDropItems={onDropItems} />
        ))}
    </div>
  )
}

export function Sidebar() {
  const { query, setQuery } = useAppState()
  const { data: folders } = useFolders()
  const { data: tags } = useTags()
  const { data: stats } = useStats()
  const { data: smartFolders } = useSmartFolders()
  const itemsResult = useItems(query)
  const invalidate = useInvalidateItems()
  const bulk = useBulkAction()
  const [tagFilter, setTagFilter] = useState('')

  const tree = useMemo(() => buildTree(folders ?? []), [folders])

  const newFolder = async () => {
    const name = prompt('New folder name')
    if (name?.trim()) {
      await api.createFolder(name.trim(), null)
      invalidate()
    }
  }

  const onDropItems = (folderId: string, ids: string[]) => {
    // dragging onto a folder ADDS membership (items can live in many folders)
    const itemFolders = new Map<string, string[]>()
    for (const item of flattenItems(itemsResult.data?.pages)) {
      if (ids.includes(item.id)) itemFolders.set(item.id, item.folder_ids)
    }
    bulk.mutate({ ids, action: { kind: 'addFolder', folderId, itemFolders } })
  }

  const saveSmartFolder = async () => {
    const name = prompt('Save current search as smart folder:')
    if (!name?.trim()) return
    await api.createSmartFolder(name.trim(), {
      q: query.q,
      tags: query.tags,
      tags_any: query.tags_any,
      untagged: query.untagged,
      folder: query.folder,
      type: query.type,
      rating: query.rating,
      order: query.order,
      created_after: query.created_after,
      created_before: query.created_before,
    })
    invalidate()
  }

  const applySmartFolder = (q: Partial<typeof emptyQuery>) => {
    setQuery({ ...emptyQuery, ...q, deleted: false })
  }

  const visibleTags = (tags ?? []).filter(
    (t) => !tagFilter || t.name.toLowerCase().includes(tagFilter.toLowerCase()),
  )

  return (
    <aside className="sidebar">
      <div className="section">
        <div
          className={`row ${!query.folder && !query.deleted ? 'active' : ''}`}
          onClick={() => setQuery({ folder: null, deleted: false })}
        >
          <span className="icon">◆</span>
          <span className="name">All items</span>
          <span className="count">{stats?.total_items ?? ''}</span>
        </div>
        <div
          className={`row ${query.folder === 'none' && !query.deleted ? 'active' : ''}`}
          onClick={() => setQuery({ folder: 'none', deleted: false })}
        >
          <span className="icon">◇</span>
          <span className="name">Unfiled</span>
        </div>
        <div
          className={`row ${query.deleted ? 'active' : ''}`}
          onClick={() => setQuery({ deleted: !query.deleted, folder: null })}
        >
          <span className="icon">🗑</span>
          <span className="name">Trash</span>
          <span className="count">{stats?.trashed_items || ''}</span>
        </div>
      </div>

      <div className="section grow">
        <div className="section-head">
          <span>Folders</span>
          <button onClick={newFolder} title="New folder">+</button>
        </div>
        {tree.map((n) => (
          <FolderRow key={n.id} node={n} depth={0} onDropItems={onDropItems} />
        ))}
        {tree.length === 0 && <div className="hint">No folders yet</div>}

        <div className="section-head" style={{ marginTop: 16 }}>
          <span>Smart folders</span>
          <button onClick={saveSmartFolder} title="Save current search as a smart folder">
            +
          </button>
        </div>
        {(smartFolders ?? []).map((s) => (
          <div key={s.id} className="row" onClick={() => applySmartFolder(s.query)}>
            <span className="icon">◎</span>
            <span className="name">{s.name}</span>
            <span className="row-actions">
              <button
                title="Rename"
                onClick={async (e) => {
                  e.stopPropagation()
                  const name = prompt('Rename smart folder', s.name)
                  if (name?.trim() && name !== s.name) {
                    await api.patchSmartFolder(s.id, { name: name.trim() })
                    invalidate()
                  }
                }}
              >
                ✎
              </button>
              <button
                title="Delete"
                onClick={async (e) => {
                  e.stopPropagation()
                  if (confirm(`Delete smart folder “${s.name}”?`)) {
                    await api.deleteSmartFolder(s.id)
                    invalidate()
                  }
                }}
              >
                🗑
              </button>
            </span>
          </div>
        ))}
        {(smartFolders?.length ?? 0) === 0 && (
          <div className="hint">Filter something, then + to save it</div>
        )}

        <div className="section-head" style={{ marginTop: 16 }}>
          <span>Tags</span>
        </div>
        {(tags?.length ?? 0) > 12 && (
          <input
            className="tag-filter"
            placeholder="Filter tags…"
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
          />
        )}
        {visibleTags.map((t) => {
          const active = query.tags.includes(t.name)
          return (
            <div
              key={t.id}
              className={`row ${active ? 'active' : ''}`}
              onClick={() =>
                setQuery({
                  tags: active ? query.tags.filter((x) => x !== t.name) : [...query.tags, t.name],
                  deleted: false,
                })
              }
            >
              <span className="icon">#</span>
              <span className="name">{t.name}</span>
              <span className="count">{t.count}</span>
            </div>
          )
        })}
        {(tags?.length ?? 0) === 0 && <div className="hint">No tags yet</div>}
      </div>

      {stats && (
        <div className="section stats">
          {stats.total_items} items · {fmtBytes(stats.total_size_bytes)}
        </div>
      )}
    </aside>
  )
}
