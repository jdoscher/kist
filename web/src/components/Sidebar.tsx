import { useEffect, useMemo, useRef, useState } from 'react'
import { api, emptyQuery, type Folder, type SmartFolder } from '../api'
import { bulkReport, useBulkAction } from '../bulk'
import { useFolders, useRefresh, useSmartFolders, useStats, useTags } from '../data'
import { useAppState } from '../state'
import { useLayer } from '../useShortcuts'
import { useWrite } from '../useWrite'
import { useDialog } from './Dialog'
import { useNotices } from './Notices'
import { isUnfiledView, smartFolderQuery, unfiledQuery } from '../query'
import { fmtBytes } from '../format'
import type { Query } from '../api'

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
  const refresh = useRefresh()
  const dialog = useDialog()
  const w = useWrite()
  const [expanded, setExpanded] = useState(depth < 1)
  const [dragOver, setDragOver] = useState(false)
  const active = query.folder === node.id && !query.deleted

  // folder writes touch no item: only the folder list refreshes
  const rename = async () => {
    const name = await dialog.prompt({ title: 'Rename folder', initial: node.name, confirmLabel: 'Rename' })
    if (!name || name === node.name) return
    void w.run('rename', () => api.patchFolder(node.id, { name }), {
      what: 'rename the folder',
      onDone: refresh.folders,
    })
  }

  const remove = async () => {
    const sub = node.children.length
      ? ` and its ${node.children.length === 1 ? 'subfolder' : `${node.children.length} subfolders`}`
      : ''
    const ok = await dialog.confirm({
      title: `Delete folder “${node.name}”${sub}?`,
      message:
        'The items inside are kept; any that end up in no folder and with no tags go back to Unfiled.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    void w.run('delete', () => api.deleteFolder(node.id), {
      what: 'delete the folder',
      onDone: () => {
        if (query.folder === node.id) setQuery({ folder: null })
        refresh.folderDeleted()
      },
    })
  }

  const addChild = async () => {
    const name = await dialog.prompt({ title: `New folder in “${node.name}”`, label: 'Name', confirmLabel: 'Create' })
    if (!name) return
    void w.run('create', () => api.createFolder(name, node.id), {
      what: 'create the folder',
      onDone: () => {
        setExpanded(true)
        refresh.folders()
      },
    })
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
  const refresh = useRefresh()
  const dialog = useDialog()
  const { notify } = useNotices()
  const w = useWrite()
  const bulk = useBulkAction()
  const [tagFilter, setTagFilter] = useState('')

  const tree = useMemo(() => buildTree(folders ?? []), [folders])

  const newFolder = async () => {
    const name = await dialog.prompt({ title: 'New folder', label: 'Name', confirmLabel: 'Create' })
    if (!name) return
    void w.run('newFolder', () => api.createFolder(name, null), {
      what: 'create the folder',
      onDone: refresh.folders,
    })
  }

  const onDropItems = (folderId: string, ids: string[]) => {
    // dragging onto a folder ADDS membership (items can live in many folders)
    bulk.mutate(
      { ids, action: { kind: 'addFolder', folderId } },
      {
        onSuccess: (r) => {
          if (!r.failed.length) return
          notify({
            key: 'bulk',
            tone: 'error',
            message: bulkReport(r),
            action: { label: 'Retry', run: () => onDropItems(folderId, r.failed.map((f) => f.id)) },
          })
        },
      },
    )
  }

  const saveSmartFolder = async () => {
    const name = await dialog.prompt({
      title: 'Save this search as a smart folder',
      label: 'Name',
      confirmLabel: 'Save',
    })
    if (!name) return
    void w.run('smartSave', () => api.createSmartFolder(name, smartFolderQuery(query)), {
      what: 'save the smart folder',
      onDone: refresh.smartFolders,
    })
  }

  const renameSmart = async (s: SmartFolder) => {
    const name = await dialog.prompt({ title: 'Rename smart folder', initial: s.name, confirmLabel: 'Rename' })
    if (!name || name === s.name) return
    void w.run(`smart:${s.id}`, () => api.patchSmartFolder(s.id, { name }), {
      what: 'rename the smart folder',
      onDone: refresh.smartFolders,
    })
  }

  const deleteSmart = async (s: SmartFolder) => {
    const ok = await dialog.confirm({
      title: `Delete smart folder “${s.name}”?`,
      message: 'Only the saved search goes; no items are touched.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    void w.run(`smart:${s.id}`, () => api.deleteSmartFolder(s.id), {
      what: 'delete the smart folder',
      onDone: refresh.smartFolders,
    })
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
                onClick={(e) => {
                  e.stopPropagation()
                  void renameSmart(s)
                }}
              >
                ✎
              </button>
              <button
                title="Delete"
                onClick={(e) => {
                  e.stopPropagation()
                  void deleteSmart(s)
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
  useLayer('modal', drawerOpen)

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
