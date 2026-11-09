import { useEffect, useMemo, useRef, useState } from 'react'
import { api, emptyQuery, type Folder } from '../api'
import {
  useAppState,
  useBulkAction,
  useFolders,
  useInvalidateItems,
  useSmartFolders,
  useStats,
  useTags,
} from '../state'
import { isUnfiledView, unfiledQuery } from '../query'
import { describeError } from '../writes'
import type { Query } from '../api'

interface TreeNode extends Folder {
  children: TreeNode[]
}

/** Run a sidebar action; on failure say why instead of failing silently.
 * Returns whether it worked. */
async function attempt(fn: () => Promise<unknown>): Promise<boolean> {
  try {
    await fn()
    return true
  } catch (e) {
    window.alert(`That didn't work: ${describeError(e)}.`)
    return false
  }
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
      if (!(await attempt(() => api.patchFolder(node.id, { name: name.trim() })))) return
      invalidate()
    }
  }

  const remove = async () => {
    const sub = node.children.length
      ? ` and its ${node.children.length === 1 ? 'subfolder' : `${node.children.length} subfolders`}`
      : ''
    const msg =
      `Delete folder “${node.name}”${sub}? The items inside are kept; ` +
      'any that end up in no folder and with no tags go back to Unfiled.'
    if (!confirm(msg)) return
    if (!(await attempt(() => api.deleteFolder(node.id)))) return
    if (query.folder === node.id) setQuery({ folder: null })
    invalidate()
  }

  const addChild = async () => {
    const name = prompt('New folder name')
    if (name?.trim()) {
      if (!(await attempt(() => api.createFolder(name.trim(), node.id)))) return
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

export function Sidebar({ mobile = false }: { mobile?: boolean }) {
  const { query, setQuery, smartId, applySmartFolder, setJobsOpen } = useAppState()
  const { data: folders } = useFolders()
  const { data: tags } = useTags()
  const { data: stats } = useStats()
  const { data: smartFolders } = useSmartFolders()
  const invalidate = useInvalidateItems()
  const bulk = useBulkAction()
  const [tagFilter, setTagFilter] = useState('')

  const tree = useMemo(() => buildTree(folders ?? []), [folders])

  const newFolder = async () => {
    const name = prompt('New folder name')
    if (name?.trim()) {
      if (!(await attempt(() => api.createFolder(name.trim(), null)))) return
      invalidate()
    }
  }

  const onDropItems = (folderId: string, ids: string[]) => {
    // dragging onto a folder ADDS membership (items can live in many folders)
    bulk.mutate(
      { ids, action: { kind: 'addFolder', folderId } },
      {
        onSuccess: (r) => {
          if (r.failed.length)
            window.alert(`Added ${r.ok} of ${ids.length} to the folder. ${r.failed[0].error}.`)
        },
      },
    )
  }

  const saveSmartFolder = async () => {
    const name = prompt('Save current search as smart folder:')
    if (!name?.trim()) return
    const saved = await attempt(() =>
      api.createSmartFolder(name.trim(), {
        q: query.q,
        tags: query.tags,
        tags_any: query.tags_any,
        untagged: query.untagged,
        view: query.view,
        folder: query.folder,
        type: query.type,
        rating: query.rating,
        order: query.order,
        created_after: query.created_after,
        created_before: query.created_before,
      }),
    )
    if (!saved) return
    invalidate()
  }

  const isAll =
    !query.folder &&
    !query.deleted &&
    !query.view &&
    !query.preview &&
    !smartId &&
    query.tags.length === 0 &&
    query.tags_any.length === 0
  const attention = (stats?.failed_jobs ?? 0) + (stats?.preview_failed ?? 0)

  const visibleTags = (tags ?? []).filter(
    (t) => !tagFilter || t.name.toLowerCase().includes(tagFilter.toLowerCase()),
  )

  return (
    <aside className="sidebar">
      <div className="section">
        <div
          className={`row ${isAll ? 'active' : ''}`}
          onClick={() => setQuery({ ...emptyQuery, order: query.order })}
          title="Show everything and clear all filters"
        >
          <span className="icon">◆</span>
          <span className="name">All items</span>
          <span className="count">{stats?.total_items ?? ''}</span>
        </div>
        <div
          className={`row ${isUnfiledView(query) ? 'active' : ''}`}
          onClick={() => setQuery({ ...emptyQuery, ...unfiledQuery, order: query.order } as Query)}
          title="Items in no folder and with no tags: your inbox"
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
        {attention > 0 && (
          <div
            className="row attention-row"
            onClick={() => setJobsOpen(true)}
            title="Imports and previews that failed"
          >
            <span className="icon">⚠</span>
            <span className="name">Needs attention</span>
            <span className="count">{attention}</span>
          </div>
        )}
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
          <div
            key={s.id}
            className={`row ${smartId === s.id ? 'active' : ''}`}
            onClick={() => applySmartFolder(s.id, s.query)}
          >
            <span className="icon">◎</span>
            <span className="name">{s.name}</span>
            <span className="row-actions">
              <button
                title="Rename"
                onClick={async (e) => {
                  e.stopPropagation()
                  const name = prompt('Rename smart folder', s.name)
                  if (name?.trim() && name !== s.name) {
                    if (!(await attempt(() => api.patchSmartFolder(s.id, { name: name.trim() })))) return
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
                    if (!(await attempt(() => api.deleteSmartFolder(s.id)))) return
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
          {mobile && ` · ${stats.tag_count} tags`}
        </div>
      )}
    </aside>
  )
}

/** Phone layout: the sidebar slides in over the grid from the ☰ button. */
export function Drawer() {
  const { query, smartId, drawerOpen, setDrawerOpen, closeDrawer } = useAppState()

  // picking a folder, smart folder or tag changes the query: close the drawer
  const mounted = useRef(false)
  useEffect(() => {
    if (mounted.current) setDrawerOpen(false)
    mounted.current = true
  }, [query, smartId, setDrawerOpen])

  return (
    <>
      <div
        className={`drawer-backdrop${drawerOpen ? ' open' : ''}`}
        onClick={closeDrawer}
      />
      <div className={`drawer${drawerOpen ? ' open' : ''}`} aria-hidden={!drawerOpen}>
        <Sidebar mobile />
      </div>
    </>
  )
}
