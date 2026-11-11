import {
  CalendarDays,
  ChevronRight,
  Copy,
  Map as MapIcon,
  Library,
  Users,
  FolderSearch,
  Hash,
  History,
  Inbox,
  LayoutGrid,
  Pencil,
  Plus,
  Settings as SettingsIcon,
  Trash2,
  TriangleAlert,
  Upload,
  X,
} from 'lucide-react'
import { useId, useMemo, useRef, useState, useEffect, type ReactNode } from 'react'
import { api, emptyQuery, type Folder, type SmartFolder } from '../api'
import { bulkReport, useBulkAction } from '../bulk'
import { useCaps, useFolders, useRefresh, useSmartFolders, useStats, useTags, useVersion } from '../data'
import { useAppState } from '../state'
import { useFocusInOut, useFocusTrap, useInertBackground } from '../useInert'
import { useLayer } from '../useShortcuts'
import { useWrite } from '../useWrite'
import { Modal, useDialog } from './Dialog'
import { useNotices } from './Notices'
import { AppearanceControls } from './Appearance'
import { useUploads } from './UploadManager'
import { isUnfiledView, smartFolderQuery, unfiledQuery } from '../query'
import { fmtBytes } from '../format'
import { useAiFlags } from './ItemAI'
import type { Query } from '../api'
import { SidebarBoards, SidebarCull } from './m3/Boards'
import { SetupChecklist } from './m3/FirstRun'
import { hasM45Filters } from '../m45/filters'
import { attentionCount } from '../attention'

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

/**
 * One sidebar row: the row div is the layout (and a drop target for
 * folders); the label part is a real button, so every row works from the
 * keyboard. Views and folders say aria-current when shown; tags, which
 * toggle, say aria-pressed.
 */
const ROW_ICON = { size: 15, strokeWidth: 1.75 } as const

function Row({
  icon,
  name,
  count,
  active,
  toggle = false,
  onActivate,
  title,
  className = '',
  style,
  before,
  actions,
  rowProps,
}: {
  icon?: ReactNode
  name: string
  count?: number | string
  active: boolean
  toggle?: boolean
  onActivate: () => void
  title?: string
  className?: string
  style?: React.CSSProperties
  before?: ReactNode
  actions?: ReactNode
  rowProps?: React.HTMLAttributes<HTMLDivElement>
}) {
  const shown = count !== undefined && count !== '' && count !== 0
  return (
    <div className={`row ${active ? 'active' : ''} ${className}`} style={style} {...rowProps}>
      {before}
      <button
        className="row-main"
        onClick={onActivate}
        title={title}
        aria-current={!toggle && active ? 'page' : undefined}
        aria-pressed={toggle ? active : undefined}
      >
        {icon && (
          <span className="icon" aria-hidden="true">
            {icon}
          </span>
        )}
        <span className="name">{name}</span>
        {shown && (
          <span className="count">
            <span className="sr-only">, </span>
            {count}
            <span className="sr-only">{count === 1 ? ' item' : ' items'}</span>
          </span>
        )}
      </button>
      {actions && <span className="row-actions">{actions}</span>}
    </div>
  )
}

const DESCRIPTION_MAX = 5000

/** Name and description of a folder (new or existing). The description
 * says what belongs in it: it's shown above the grid, and agents read it
 * in list_folders before filing. */
function FolderDialog({
  title,
  confirmLabel,
  initialName = '',
  initialDescription = '',
  onDone,
}: {
  title: string
  confirmLabel: string
  initialName?: string
  initialDescription?: string
  onDone: (v: { name: string; description: string } | null) => void
}) {
  const [name, setName] = useState(initialName)
  const [description, setDescription] = useState(initialDescription)
  const id = useId()
  const ok = name.trim().length > 0 && description.length <= DESCRIPTION_MAX
  return (
    <Modal title={title} onClose={() => onDone(null)} className="dialog" headerClose={false}>
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) onDone({ name: name.trim(), description: description.trim() })
        }}
      >
        <label htmlFor={`${id}-name`}>Name</label>
        <input id={`${id}-name`} data-autofocus value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor={`${id}-desc`}>Description (optional)</label>
        <textarea
          id={`${id}-desc`}
          rows={4}
          value={description}
          maxLength={DESCRIPTION_MAX}
          aria-describedby={`${id}-desc-hint`}
          placeholder="What belongs in this folder?"
          onChange={(e) => setDescription(e.target.value)}
        />
        <p id={`${id}-desc-hint`} className="hint">
          Shown above the folder’s items. Agents read it too, so they know what to file here.
        </p>
        <div className="dialog-actions">
          <button type="button" onClick={() => onDone(null)}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={!ok}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** Open a FolderDialog and wait for its answer. */
function useFolderDialog() {
  const [req, setReq] = useState<{
    props: Omit<Parameters<typeof FolderDialog>[0], 'onDone'>
    resolve: (v: { name: string; description: string } | null) => void
  } | null>(null)
  const ask = (props: Omit<Parameters<typeof FolderDialog>[0], 'onDone'>) =>
    new Promise<{ name: string; description: string } | null>((resolve) => setReq({ props, resolve }))
  const element = req ? (
    <FolderDialog
      {...req.props}
      onDone={(v) => {
        req.resolve(v)
        setReq(null)
      }}
    />
  ) : null
  return { ask, element }
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
  const folderDialog = useFolderDialog()
  const { canEdit } = useCaps()
  const w = useWrite()
  const [expanded, setExpanded] = useState(depth < 1)
  const [dragOver, setDragOver] = useState(false)
  const active = query.folder === node.id && !query.deleted

  // folder writes touch no item: only the folder list refreshes
  const rename = async () => {
    const v = await folderDialog.ask({
      title: 'Edit folder',
      confirmLabel: 'Save',
      initialName: node.name,
      initialDescription: node.description ?? '',
    })
    if (!v || (v.name === node.name && v.description === (node.description ?? ''))) return
    void w.run('rename', () => api.patchFolder(node.id, { name: v.name, description: v.description }), {
      what: 'save the folder',
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
    const v = await folderDialog.ask({ title: `New folder in “${node.name}”`, confirmLabel: 'Create' })
    if (!v) return
    void w.run('create', () => api.createFolder(v.name, node.id, v.description), {
      what: 'create the folder',
      onDone: () => {
        setExpanded(true)
        refresh.folders()
      },
    })
  }

  const hasChildren = node.children.length > 0
  return (
    <div>
      <Row
        className={`folder-row ${dragOver ? 'drag-over' : ''}`}
        style={{ paddingLeft: 4 + depth * 14 }}
        name={node.name}
        count={node.item_count || ''}
        active={active}
        onActivate={() => setQuery({ folder: active ? null : node.id, deleted: false })}
        before={
          <button
            className={`twisty ${hasChildren ? '' : 'hidden'} ${expanded ? 'open' : ''}`}
            aria-label={`${expanded ? 'Collapse' : 'Expand'} ${node.name}`}
            aria-expanded={hasChildren ? expanded : undefined}
            tabIndex={hasChildren ? undefined : -1}
            onClick={() => setExpanded(!expanded)}
          >
            <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
          </button>
        }
        actions={
          canEdit && <>
            <button title="New subfolder" aria-label={`New subfolder in ${node.name}`} onClick={() => void addChild()}>
              <Plus size={14} strokeWidth={2} aria-hidden="true" />
            </button>
            <button title="Rename or describe" aria-label={`Edit ${node.name}`} onClick={() => void rename()}>
              <Pencil size={13} strokeWidth={1.75} aria-hidden="true" />
            </button>
            <button title="Delete" aria-label={`Delete ${node.name}`} onClick={() => void remove()}>
              <Trash2 size={13} strokeWidth={1.75} aria-hidden="true" />
            </button>
          </>
        }
        rowProps={{
          onDragOver: (e) => {
            if (e.dataTransfer.types.includes('application/x-kist-items')) {
              e.preventDefault()
              setDragOver(true)
            }
          },
          onDragLeave: () => setDragOver(false),
          onDrop: (e) => {
            e.preventDefault()
            setDragOver(false)
            const raw = e.dataTransfer.getData('application/x-kist-items')
            if (raw) onDropItems(node.id, JSON.parse(raw))
          },
        }}
      />
      {expanded &&
        node.children.map((c) => (
          <FolderRow key={c.id} node={c} depth={depth + 1} onDropItems={onDropItems} />
        ))}
      {folderDialog.element}
    </div>
  )
}

/** Phone: the one way to add from the phone (decision 3b keeps editing on
 * the desktop; Jay approved upload on phones). iOS offers Photos here. */
function PhoneUpload() {
  const uploads = useUploads()
  const { closeDrawer } = useAppState()
  const fileRef = useRef<HTMLInputElement>(null)
  return (
    <>
      <Row icon={<Upload {...ROW_ICON} />} name="Upload photos or files" active={false} onActivate={() => fileRef.current?.click()} />
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        aria-hidden="true"
        tabIndex={-1}
        onChange={(e) => {
          if (e.target.files?.length) {
            uploads.enqueue(Array.from(e.target.files))
            closeDrawer()
          }
          e.target.value = ''
        }}
      />
    </>
  )
}

export function Sidebar({ mobile = false }: { mobile?: boolean }) {
  const { query, setQuery, smartId, applySmartFolder, setJobsOpen, setRecentOpen, openSettings, settings, closeDrawer, organise, openOrganise, screen, openScreen } = useAppState()
  const aiFlags = useAiFlags()
  const caps = useCaps()
  const folderDialog = useFolderDialog()
  const { data: folders } = useFolders()
  const { data: tags } = useTags()
  const { data: stats } = useStats()
  const { data: smartFolders } = useSmartFolders()
  const { data: version } = useVersion()
  const refresh = useRefresh()
  const dialog = useDialog()
  const { notify } = useNotices()
  const w = useWrite()
  const bulk = useBulkAction()
  const [tagFilter, setTagFilter] = useState('')

  const tree = useMemo(() => buildTree(folders ?? []), [folders])

  const newFolder = async () => {
    const v = await folderDialog.ask({ title: 'New folder', confirmLabel: 'Create' })
    if (!v) return
    void w.run('newFolder', () => api.createFolder(v.name, null, v.description), {
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
            action: r.failed.some((f) => f.retryable !== false)
              ? { label: 'Retry', run: () => onDropItems(folderId, r.failed.filter((f) => f.retryable !== false).map((f) => f.id)) }
              : undefined,
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
    const name = await dialog.prompt({ title: 'Rename smart folder', label: 'Name', initial: s.name, confirmLabel: 'Rename' })
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
    !query.fixity &&
    !smartId &&
    !settings &&
    !organise &&
    !screen &&
    !hasM45Filters(query) &&
    !query.similar &&
    !query.color &&
    !query.image &&
    !query.person &&
    query.tags.length === 0 &&
    query.tags_any.length === 0
  const attention = attentionCount(stats)

  const visibleTags = (tags ?? []).filter(
    (t) => !tagFilter || t.name.toLowerCase().includes(tagFilter.toLowerCase()),
  )

  const versionText = version ? `Kist ${version.version}${version.commit !== 'unknown' ? ` (${version.commit.slice(0, 12)})` : ''}` : ''

  return (
    <aside className="sidebar" aria-label="Library navigation">
      <nav className="section" aria-label="Views">
        {mobile && caps.canEdit && <PhoneUpload />}
        <Row
          icon={<LayoutGrid {...ROW_ICON} />}
          name="All items"
          count={stats?.total_items ?? ''}
          active={isAll}
          onActivate={() => setQuery({ ...emptyQuery, order: query.order })}
          title="Show everything and clear all filters"
        />
        {!caps.restricted && (
          <>
            <Row
              icon={<Inbox {...ROW_ICON} />}
              name="Unfiled"
              active={isUnfiledView(query) && !smartId && !settings}
              onActivate={() => setQuery({ ...emptyQuery, ...unfiledQuery, order: query.order } as Query)}
              title="Items with no folder and no tag: your inbox"
            />
            <Row
              icon={<Trash2 {...ROW_ICON} />}
              name="Trash"
              count={stats?.trashed_items || ''}
              active={query.deleted && !smartId && !settings}
              onActivate={() => setQuery({ deleted: !query.deleted, folder: null })}
            />
          </>
        )}
        {!caps.restricted && (
          <>
            <Row
              icon={<Copy {...ROW_ICON} />}
              name="Duplicates"
              active={organise === 'duplicates' || organise === 'stacks'}
              onActivate={() => openOrganise('duplicates')}
              title="Review duplicate pictures and stack suggestions"
            />
            <Row
              icon={<Library {...ROW_ICON} />}
              name="Collections"
              active={organise === 'collections'}
              onActivate={() => openOrganise('collections')}
              title="You seem to collect…: groups of look-alike items to keep as folders"
            />
            {aiFlags.faces && (
              <Row
                icon={<Users {...ROW_ICON} />}
                name="People"
                active={organise === 'people' || !!query.person}
                onActivate={() => openOrganise('people')}
                title="Faces grouped into people"
              />
            )}
          </>
        )}
        <Row
          icon={<CalendarDays {...ROW_ICON} />}
          name="Timeline"
          active={screen?.kind === 'timeline'}
          onActivate={() => openScreen({ kind: 'timeline', group: 'month' })}
          title="Everything by the date it was taken, with On this day"
        />
        <Row
          icon={<MapIcon {...ROW_ICON} />}
          name="Map"
          active={screen?.kind === 'map'}
          onActivate={() => openScreen({ kind: 'map' })}
          title="Photos and videos with a location, on a map"
        />
        {attention > 0 && (
          <Row
            className="attention-row"
            icon={<TriangleAlert {...ROW_ICON} />}
            name="Needs attention"
            count={attention}
            active={false}
            onActivate={() => setJobsOpen(true)}
            title="Imports, previews and fixity checks that failed, and source pages that are gone"
          />
        )}
        {mobile && caps.canEdit && (
          <Row icon={<History {...ROW_ICON} />} name="Recent changes"
            active={false}
            onActivate={() => {
              closeDrawer()
              setRecentOpen(true)
            }}
          />
        )}
        {mobile && caps.isAdmin && (
          <Row
            icon={<SettingsIcon {...ROW_ICON} />}
            name="Settings"
            active={!!settings}
            onActivate={() => openSettings()}
          />
        )}
      </nav>

      <div className="section grow">
        {!mobile && <SetupChecklist />}
        {/* boards sit above the long folder and tag lists so they don't get buried */}
        <SidebarBoards first />
        <div className="section-head" style={{ marginTop: 16 }}>
          <h2>Folders</h2>
          {caps.canEdit && (
            <button className="icon-btn" onClick={() => void newFolder()} title="New folder" aria-label="New folder">
              <Plus size={14} strokeWidth={2} aria-hidden="true" />
            </button>
          )}
        </div>
        {folderDialog.element}
        {tree.map((n) => (
          <FolderRow key={n.id} node={n} depth={0} onDropItems={onDropItems} />
        ))}
        {tree.length === 0 && (
          <div className="hint">{mobile || !caps.canEdit ? 'No folders yet.' : 'No folders yet. Press + to make one.'}</div>
        )}

        {!caps.restricted && (
          <>
        <div className="section-head" style={{ marginTop: 16 }}>
          <h2>Smart folders</h2>
          {caps.canEdit && <button
            className="icon-btn"
            onClick={() => void saveSmartFolder()}
            title="Save this search as a smart folder"
            aria-label="Save this search as a smart folder"
          >
            <Plus size={14} strokeWidth={2} aria-hidden="true" />
          </button>}
        </div>
        {(smartFolders ?? []).map((s) => (
          <Row
            key={s.id}
            icon={<FolderSearch {...ROW_ICON} />}
            name={s.name}
            active={smartId === s.id}
            onActivate={() => applySmartFolder(s.id, s.query)}
            actions={
              caps.canEdit && <>
                <button title="Rename" aria-label={`Rename smart folder ${s.name}`} onClick={() => void renameSmart(s)}>
                  <Pencil size={13} strokeWidth={1.75} aria-hidden="true" />
                </button>
                <button title="Delete" aria-label={`Delete smart folder ${s.name}`} onClick={() => void deleteSmart(s)}>
                  <Trash2 size={13} strokeWidth={1.75} aria-hidden="true" />
                </button>
              </>
            }
          />
        ))}
        {(smartFolders?.length ?? 0) === 0 && (
          <div className="hint">
            {mobile || !caps.canEdit ? 'No saved searches yet.' : 'Save a search: filter the grid, then press + above.'}
          </div>
        )}

        <SidebarCull />

        <div className="section-head" style={{ marginTop: 16 }}>
          <h2>Tags</h2>
        </div>
        {(tags?.length ?? 0) > 12 && (
          <input
            className="tag-filter"
            placeholder="Filter tags…"
            aria-label="Filter tags"
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
          />
        )}
        {visibleTags.map((t) => {
          const active = query.tags.includes(t.name)
          return (
            <Row
              key={t.id}
              icon={<Hash {...ROW_ICON} />}
              name={t.name}
              count={t.count}
              active={active}
              toggle
              onActivate={() =>
                setQuery({
                  tags: active ? query.tags.filter((x) => x !== t.name) : [...query.tags, t.name],
                  deleted: false,
                })
              }
            />
          )
        })}
        {(tags?.length ?? 0) === 0 && (
          <div className="hint">{caps.canEdit ? 'No tags yet. Add them in an item’s info panel.' : 'No tags yet.'}</div>
        )}
          </>
        )}
      </div>

      {mobile && (
        <section className="section" aria-labelledby="appearance-head">
          <div className="section-head">
            <h2 id="appearance-head">Appearance</h2>
          </div>
          <AppearanceControls />
        </section>
      )}
      {stats && (
        <div className="section stats" title={versionText || undefined}>
          {stats.total_items} items · {fmtBytes(stats.total_size_bytes)}
          {mobile && ` · ${stats.tag_count} tags`}
          {versionText && <div className="version">{versionText}</div>}
        </div>
      )}
    </aside>
  )
}

/** Phone layout: the sidebar slides in over the grid from the menu button. A
 * modal dialog while open (the page behind is inert, Escape or ✕ closes
 * it, focus goes back to ☰); inert when closed, so Tab never walks the
 * hidden rows. */
export function Drawer() {
  const { query, smartId, drawerOpen, setDrawerOpen, closeDrawer } = useAppState()
  useLayer('modal', drawerOpen)
  const layerRef = useRef<HTMLDivElement>(null)
  const drawerRef = useRef<HTMLDivElement>(null)
  useInertBackground(layerRef, drawerOpen)
  useFocusTrap(drawerRef, drawerOpen)
  useFocusInOut(drawerRef, drawerOpen, {
    initial: () => drawerRef.current?.querySelector<HTMLElement>('.row-main'),
    restore: () => document.querySelector<HTMLElement>('.menu-btn'),
  })

  // picking a folder, smart folder or tag changes the query: close the drawer
  const mounted = useRef(false)
  useEffect(() => {
    if (mounted.current) setDrawerOpen(false)
    mounted.current = true
  }, [query, smartId, setDrawerOpen])

  return (
    <div ref={layerRef} className="drawer-layer">
      <div className={`drawer-backdrop${drawerOpen ? ' open' : ''}`} onClick={closeDrawer} />
      <div
        ref={drawerRef}
        id="folder-drawer"
        className={`drawer${drawerOpen ? ' open' : ''}`}
        role={drawerOpen ? 'dialog' : undefined}
        aria-modal={drawerOpen ? 'true' : undefined}
        aria-label="Folders and tags"
        inert={!drawerOpen}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation()
            closeDrawer()
          }
        }}
      >
        <div className="drawer-head">
          <button className="drawer-close" aria-label="Close menu" onClick={closeDrawer}>
            <X size={18} strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>
        <Sidebar mobile />
      </div>
    </div>
  )
}
