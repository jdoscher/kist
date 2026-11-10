// ⌘K / Ctrl+K: one box that goes anywhere and does anything. An ARIA
// combobox: focus stays in the input, ↑/↓ move the active row
// (aria-activedescendant), Enter runs it, Escape closes. On a phone it's a
// full-height sheet.

import { useQuery } from '@tanstack/react-query'
import {
  Columns2,
  FileImage,
  Folder,
  FolderSearch,
  Hash,
  History,
  Inbox,
  LayoutDashboard,
  LayoutGrid,
  ListChecks,
  Moon,
  PencilLine,
  Search,
  Settings,
  Sparkles,
  Trash2,
  Upload,
  User,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { emptyQuery, type Query } from '../../api'
import { api3 } from '../../creativeApi'
import { useCaps, useSmartFolders } from '../../data'
import { addActionOpens, KIND_LABEL, mergeResults, moveActive, pushRecent, readRecent, type PaletteEntry, type PaletteKind } from '../../palette'
import { unfiledQuery } from '../../query'
import { useAppState } from '../../state'
import { Modal, useDialog } from '../Dialog'
import { useUploads } from '../UploadManager'
import { useCreative } from './Creative'

const KIND_ICON: Record<PaletteKind, LucideIcon> = {
  recent: History,
  action: Zap,
  folder: Folder,
  smart_folder: FolderSearch,
  board: LayoutDashboard,
  tag: Hash,
  person: User,
  item: FileImage,
}

const ACTION_ICON: Record<string, LucideIcon> = {
  upload: Upload,
  'new-board': LayoutDashboard,
  boards: LayoutDashboard,
  triage: ListChecks,
  compare: Columns2,
  survey: Columns2,
  rename: PencilLine,
  'apply-template': Sparkles,
  history: History,
  trash: Trash2,
  settings: Settings,
  theme: Moon,
  all: LayoutGrid,
  unfiled: Inbox,
  picks: Sparkles,
}

export default function CommandPalette({ onClose }: { onClose: () => void }) {
  const st = useAppState()
  const { selection, setQuery, applySmartFolder, openScreen, openSettings, setRecentOpen, theme, setTheme, selectOnly, setPreviewId } = st
  const creative = useCreative()
  const caps = useCaps()
  const dialog = useDialog()
  const uploads = useUploads()
  const { data: smartFolders } = useSmartFolders()
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [active, setActive] = useState(0)
  const listId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const recent = useMemo(() => readRecent(), [])

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 120)
    return () => clearTimeout(t)
  }, [q])

  const server = useQuery({
    queryKey: ['palette', debounced],
    queryFn: ({ signal }) => api3.palette(debounced, signal),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  })

  const sel = [...selection]
  const local: PaletteEntry[] = useMemo(() => {
    const a: PaletteEntry[] = [
      { kind: 'action', id: 'all', title: 'Go to All items', keywords: 'library everything' },
      { kind: 'action', id: 'unfiled', title: 'Go to Unfiled', keywords: 'inbox' },
      { kind: 'action', id: 'boards', title: 'Go to Boards', keywords: 'moodboard canvas' },
      { kind: 'action', id: 'trash', title: 'Go to Trash' },
      { kind: 'action', id: 'picks', title: 'Show picks', keywords: 'flag pick filter' },
    ]
    if (caps.canEdit) {
      a.push(
        { kind: 'action', id: 'new-board', title: 'New board', subtitle: 'Start a moodboard', keywords: 'create canvas' },
        { kind: 'action', id: 'triage', title: 'Triage Unfiled', subtitle: 'One by one: pick, rate, tag, file', keywords: 'cull inbox' },
        { kind: 'action', id: 'upload', title: 'Add files', subtitle: 'Upload from this device', keywords: 'upload import' },
        { kind: 'action', id: 'history', title: 'Recent changes', subtitle: 'Undo', keywords: 'history undo' },
      )
    }
    if (sel.length >= 2) {
      a.unshift(
        { kind: 'action', id: 'compare', title: `Compare ${Math.min(sel.length, 4)} selected`, subtitle: 'Side by side, wipe or overlay', keywords: 'diff' },
        { kind: 'action', id: 'survey', title: `Survey ${sel.length} selected`, subtitle: 'Pick and reject, one by one', keywords: 'cull' },
      )
    }
    if (sel.length && caps.canEdit) {
      a.push(
        { kind: 'action', id: 'rename', title: `Rename ${sel.length} selected…`, subtitle: 'With a pattern', keywords: 'bulk' },
        { kind: 'action', id: 'apply-template', title: `Apply template to ${sel.length}…`, keywords: 'metadata credit license' },
      )
    }
    for (const t of ['dark', 'light', 'gallery', 'system'] as const) {
      if (t !== theme) a.push({ kind: 'action', id: `theme:${t}`, title: t === 'system' ? 'Theme: follow the system' : `Theme: ${t}`, subtitle: `Now: ${theme}`, keywords: 'appearance colour mode' })
    }
    if (caps.isAdmin) a.push({ kind: 'action', id: 'settings', title: 'Settings', keywords: 'tokens templates archive' })
    return a
  }, [sel.length, caps.canEdit, caps.isAdmin, theme]) // eslint-disable-line react-hooks/exhaustive-deps

  // the server's actions we can run; the rest are dropped
  const KNOWN_ACTIONS = new Set(local.map((l) => l.id).concat(['add-url', 'new-folder']))
  const serverEntries = (server.data ?? []).filter((e) => e.kind !== 'action' || KNOWN_ACTIONS.has(e.id))
  // a recent action that no longer applies (e.g. the theme you're on now) is dropped
  const liveRecent = recent.filter((r) => r.kind !== 'action' || local.some((l) => l.id === r.id))
  const groups = mergeResults(q, local, serverEntries, liveRecent)
  const flat = groups.flatMap((g) => g.entries.map((e) => ({ ...e, group: g.kind })))
  const cur = Math.min(active, flat.length - 1)
  useEffect(() => setActive(0), [q, debounced])

  useEffect(() => {
    document.getElementById(`${listId}-${cur}`)?.scrollIntoView({ block: 'nearest' })
  }, [cur, listId])

  const runAction = async (id: string) => {
    switch (id) {
      case 'all':
        return setQuery({ ...emptyQuery })
      case 'unfiled':
        return setQuery({ ...emptyQuery, ...unfiledQuery } as Query)
      case 'trash':
        return setQuery({ ...emptyQuery, deleted: true })
      case 'picks':
        return setQuery({ ...emptyQuery, flag: 'pick' })
      case 'boards':
        return openScreen({ kind: 'boards' })
      case 'new-board': {
        const name = await dialog.prompt({ title: 'New board', label: 'Name', initial: 'Untitled board', confirmLabel: 'Create' })
        if (!name) return
        const b = await api3.createBoard({ name, item_ids: sel })
        return openScreen({ kind: 'board', id: b.id })
      }
      case 'triage':
        return openScreen({ kind: 'triage', view: 'unfiled' })
      case 'compare':
        return creative.openCompare(sel, 'compare')
      case 'survey':
        return creative.openCompare(sel, 'survey')
      case 'rename':
        return creative.openRename(sel)
      case 'apply-template':
        return creative.openTemplate(sel)
      case 'history':
        return setRecentOpen(true)
      case 'settings':
        return openSettings()
      case 'theme:dark':
      case 'theme:light':
      case 'theme:gallery':
      case 'theme:system':
        return setTheme(id.slice(6) as 'dark' | 'light' | 'gallery' | 'system')
      case 'upload':
      case 'add-url': {
        if (addActionOpens(id) === 'url') return uploads.askUrl()
        const input = document.createElement('input')
        input.type = 'file'
        input.multiple = true
        input.onchange = () => input.files?.length && uploads.enqueue(Array.from(input.files))
        input.click()
        return
      }
      case 'new-folder':
        return setQuery({ ...emptyQuery }) // the sidebar's + makes folders
    }
  }

  const run = async (e: PaletteEntry & { group?: PaletteKind }) => {
    onClose()
    if (e.kind !== 'item') pushRecent(e)
    switch (e.kind) {
      case 'action':
        return runAction(e.id)
      case 'folder':
        return setQuery({ ...emptyQuery, folder: e.id })
      case 'smart_folder': {
        const s = smartFolders?.find((x) => x.id === e.id)
        if (s) applySmartFolder(s.id, s.query)
        return
      }
      case 'tag':
        return setQuery({ ...emptyQuery, tags: [e.title] })
      case 'board':
        return openScreen({ kind: 'board', id: e.id })
      case 'person':
        return setQuery({ ...emptyQuery, q: e.title })
      case 'item':
        setQuery({ ...emptyQuery })
        selectOnly(e.id)
        setPreviewId(e.id)
        return
      case 'recent':
        return
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(moveActive(cur, e.key === 'ArrowDown' ? 1 : -1, flat.length))
    } else if (e.key === 'Home' && e.ctrlKey) {
      setActive(0)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const pick = flat[cur]
      if (pick) void run(pick)
    }
  }

  let i = -1
  return (
    <Modal title="Go to or do anything" onClose={onClose} className="palette" headerClose={st.mobile}>
      <div className="palette-input">
        <Search size={16} strokeWidth={1.75} aria-hidden="true" />
        <input
          ref={inputRef}
          data-autofocus
          role="combobox"
          aria-expanded={flat.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={flat.length ? `${listId}-${cur}` : undefined}
          aria-label="Search folders, tags, boards, items and actions"
          placeholder="Folders, tags, boards, items, actions…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
          autoComplete="off"
          spellCheck={false}
        />
        {!st.mobile && <kbd className="palette-esc">esc</kbd>}
      </div>
      <div id={listId} role="listbox" aria-label="Results" className="palette-list">
        {groups.map((g) => (
          <div key={g.kind} role="group" aria-labelledby={`${listId}-g-${g.kind}`} className="palette-group">
            <div id={`${listId}-g-${g.kind}`} className="palette-group-label" role="presentation">
              {KIND_LABEL[g.kind]}
            </div>
            {g.entries.map((e) => {
              i++
              const idx = i
              const kind = g.kind === 'recent' ? e.kind : g.kind
              const Icon = (kind === 'action' && ACTION_ICON[e.id]) || KIND_ICON[kind as PaletteKind] || Zap
              return (
                <div
                  key={`${g.kind}:${e.kind}:${e.id}`}
                  id={`${listId}-${idx}`}
                  role="option"
                  aria-selected={idx === cur}
                  className={`palette-option${idx === cur ? ' active' : ''}`}
                  onMouseMove={() => idx !== cur && setActive(idx)}
                  onMouseDown={(ev) => ev.preventDefault()}
                  onClick={() => void run(e)}
                >
                  <Icon size={15} strokeWidth={1.75} aria-hidden="true" />
                  <span className="palette-title">{e.kind === 'tag' ? `#${e.title}` : e.title}</span>
                  {e.subtitle && <span className="palette-sub">{e.subtitle}</span>}
                </div>
              )
            })}
          </div>
        ))}
        {!flat.length && (
          <p className="palette-empty" role="status">
            {server.isFetching ? 'Searching…' : `Nothing matches “${q}”.`}
          </p>
        )}
      </div>
      <p className="palette-hint" aria-hidden="true">
        ↑↓ move · Enter open · Esc close
      </p>
      <span className="sr-only" role="status" aria-live="polite">
        {q ? `${flat.length} results` : ''}
      </span>
    </Modal>
  )
}
