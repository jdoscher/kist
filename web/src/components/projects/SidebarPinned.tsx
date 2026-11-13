// F6: the sidebar's Pinned section (projects, folders, smart folders,
// boards, people, tags and saved searches; drag or Alt+↑/↓ to reorder),
// the Projects row for the Views list, and a pin toggle for other rows.

import { useQueryClient } from '@tanstack/react-query'
import {
  Briefcase,
  FolderClosed,
  FolderSearch,
  GripVertical,
  Hash,
  Pin as PinIcon,
  PinOff,
  Plus,
  Presentation,
  Search,
  User,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { emptyQuery, type Query } from '../../api'
import { useCaps, useFolderNames, useSmartFolders } from '../../data'
import { isPinned, movePin, pinForPlace, projectsApi, searchPinUrl, type Pin, type PinKind } from '../../projects'
import { clearedM45 } from '../../m45/filters'
import { hasNarrowing } from '../../query'
import { useAppState } from '../../state'
import { fromUrl, toUrl } from '../../url'
import { useWrite } from '../../useWrite'
import { useDialog } from '../Dialog'
import { useBoards } from '../m3/Boards'
import { useNotices } from '../Notices'
import { usePins, useProjects } from './hooks'

const ICON = { size: 16 } as const

const KIND_ICON: Record<PinKind, ReactNode> = {
  project: <Briefcase {...ICON} />,
  folder: <FolderClosed {...ICON} />,
  smart: <FolderSearch {...ICON} />,
  board: <Presentation {...ICON} />,
  person: <User {...ICON} />,
  tag: <Hash {...ICON} />,
  search: <Search {...ICON} />,
}

const KIND_WORD: Record<PinKind, string> = {
  project: 'project',
  folder: 'folder',
  smart: 'smart folder',
  board: 'board',
  person: 'person',
  tag: 'tag',
  search: 'saved search',
}

function sortedUrl(url: string): string {
  const [path, qs] = url.split('?')
  const s = new URLSearchParams(qs ?? '')
  s.sort()
  return `${path}?${s}`
}

/** Open what a pin points at. */
function usePinActions() {
  const { query, setQuery, applySmartFolder, openScreen, smartId, screen } = useAppState()
  const here = sortedUrl(toUrl({ query, smartId, item: null, full: false }))
  // a pin opens a fresh view: no filters left over from the last one
  const fresh = { ...emptyQuery, ...clearedM45(), order: query.order }
  const open = (p: Pin) => {
    switch (p.kind) {
      case 'project':
        return openScreen({ kind: 'project', id: p.ref })
      case 'board':
        return openScreen({ kind: 'board', id: p.ref })
      case 'folder':
        return setQuery({ ...fresh, folder: p.ref })
      case 'smart':
        return applySmartFolder(p.ref, (p.query as Partial<Query>) ?? {})
      case 'person':
        return setQuery({ ...fresh, person: p.ref })
      case 'tag':
        return setQuery({ ...fresh, tags: [p.ref] })
      case 'search': {
        const url = searchPinUrl(String(p.query ?? ''))
        const [path, qs] = url.split('?')
        return setQuery({ ...emptyQuery, ...clearedM45(), ...fromUrl(path, qs ? `?${qs}` : '').query })
      }
    }
  }
  const active = (p: Pin): boolean => {
    if (p.kind === 'project') return screen?.kind === 'project' && screen.id === p.ref
    if (screen) return false
    if (p.kind === 'smart') return smartId === p.ref
    if (smartId) return false
    if (p.kind === 'folder') return query.folder === p.ref && !query.deleted && !hasNarrowing(query)
    if (p.kind === 'person') return query.person === p.ref && !query.folder
    if (p.kind === 'tag') return query.tags.length === 1 && query.tags[0].toLowerCase() === p.ref.toLowerCase() && !query.folder
    if (p.kind === 'search') {
      const url = searchPinUrl(String(p.query ?? ''))
      const [path, qs] = url.split('?')
      return sortedUrl(toUrl({ ...fromUrl(path, qs ? `?${qs}` : ''), item: null, full: false })) === here
    }
    return false
  }
  return { open, active }
}

export function SidebarPinned() {
  const caps = useCaps()
  const pins = usePins()
  const { open, active } = usePinActions()
  const qc = useQueryClient()
  const w = useWrite()
  const [drag, setDragState] = useState<number | null>(null)
  const [over, setOver] = useState<number | null>(null)
  // the dragged row, readable before React re-renders (dragover can come first)
  const dragRef = useRef<number | null>(null)
  const setDrag = (i: number | null) => {
    dragRef.current = i
    setDragState(i)
  }
  const list = pins.data ?? []
  if (pins.isError || (!list.length && !caps.canEdit)) return null

  const save = (next: Pin[]) => {
    qc.setQueryData(['pins'], next)
    void w.run('order', () => projectsApi.reorderPins(next.map((p) => ({ kind: p.kind, ref: p.ref }))), {
      what: 'reorder the pins',
      onDone: (fresh) => qc.setQueryData(['pins'], fresh),
      onError: () => {
        void qc.invalidateQueries({ queryKey: ['pins'] })
        return false
      },
    })
  }
  const unpin = (p: Pin) =>
    void w.run(`pin:${p.kind}:${p.ref}`, () => projectsApi.unpin(p.kind, p.ref), {
      what: 'unpin it',
      onDone: (fresh) => qc.setQueryData(['pins'], fresh),
    })

  return (
    <section className="pj-pinned" aria-labelledby="pj-pinned-head">
      <div className="section-head">
        <h2 id="pj-pinned-head">Pinned</h2>
        {caps.canEdit && <PinMenu pins={list} />}
      </div>
      {list.length === 0 && <div className="hint">Pin projects, folders, boards or a search here with +.</div>}
      <ul className="pj-pin-list" aria-describedby={caps.canEdit && list.length > 1 ? 'pj-pin-reorder' : undefined}>
        {list.map((p, i) => {
          const on = active(p)
          return (
            <li
              key={`${p.kind}:${p.ref}`}
              className={`row pj-pin-row${on ? ' active' : ''}${over === i && drag !== null && drag !== i ? ' pj-over' : ''}${drag === i ? ' pj-dragging' : ''}`}
              draggable={caps.canEdit}
              onDragStart={(e) => {
                setDrag(i)
                e.dataTransfer.effectAllowed = 'move'
                e.dataTransfer.setData('application/x-kist-pin', String(i))
              }}
              onDragOver={(e) => {
                if (dragRef.current === null) return
                e.preventDefault()
                setOver(i)
              }}
              onDragLeave={() => setOver((o) => (o === i ? null : o))}
              onDrop={(e) => {
                e.preventDefault()
                const from = dragRef.current
                if (from !== null && from !== i) save(movePin(list, from, i))
                setDrag(null)
                setOver(null)
              }}
              onDragEnd={() => {
                setDrag(null)
                setOver(null)
              }}
            >
              {caps.canEdit && (
                <span className="pj-grip" aria-hidden="true">
                  <GripVertical size={14} />
                </span>
              )}
              <button
                className="row-main"
                aria-current={on ? 'page' : undefined}
                title={`Open the ${KIND_WORD[p.kind]} “${p.name}”${caps.canEdit ? ' (drag, or Alt+↑/↓, to reorder)' : ''}`}
                onClick={() => open(p)}
                onKeyDown={(e) => {
                  if (!caps.canEdit || !e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return
                  e.preventDefault()
                  const to = e.key === 'ArrowUp' ? i - 1 : i + 1
                  if (to < 0 || to >= list.length) return
                  save(movePin(list, i, to))
                  // keep focus on the moved row
                  requestAnimationFrame(() =>
                    document.querySelectorAll<HTMLButtonElement>('.pj-pin-row .row-main')[to]?.focus(),
                  )
                }}
              >
                <span className="icon" aria-hidden="true">
                  {KIND_ICON[p.kind]}
                </span>
                <span className="name">{p.name}</span>
                <span className="sr-only">, {KIND_WORD[p.kind]}</span>
              </button>
              {caps.canEdit && (
                <span className="row-actions">
                  <button title="Unpin" aria-label={`Unpin ${p.name}`} onClick={() => unpin(p)}>
                    <PinOff size={14} aria-hidden="true" />
                  </button>
                </span>
              )}
            </li>
          )
        })}
      </ul>
      {caps.canEdit && list.length > 1 && (
        <span id="pj-pin-reorder" className="sr-only">
          Alt plus up or down arrow moves a pin.
        </span>
      )}
    </section>
  )
}

/** The + in the Pinned head: pin this view, this search, a project or a board. */
function PinMenu({ pins }: { pins: Pin[] }) {
  const { query, smartId, screen } = useAppState()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const qc = useQueryClient()
  const w = useWrite()
  const dialog = useDialog()
  const { notify } = useNotices()
  const projects = useProjects('active')
  const boards = useBoards()
  const folderNames = useFolderNames()
  const { data: smart } = useSmartFolders()
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', esc, true)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', esc, true)
    }
  }, [open])

  const target = pinForPlace({ query, smartId, screen: screen as { kind: string; id?: string } | null })
  const targetName = !target
    ? ''
    : target.kind === 'folder'
      ? folderNames.get(target.ref) ?? 'this folder'
      : target.kind === 'smart'
        ? smart?.find((s) => s.id === target.ref)?.name ?? 'this smart folder'
        : target.kind === 'tag'
          ? `#${target.ref}`
          : target.kind === 'project'
            ? projects.data?.find((p) => p.id === target.ref)?.name ?? 'this project'
            : 'this view'
  const searchable = !target && !screen && hasNarrowing(query)

  const pin = (body: Parameters<typeof projectsApi.pin>[0]) => {
    setOpen(false)
    void w.run('pin', () => projectsApi.pin(body), {
      what: 'pin it',
      onDone: (fresh) => {
        qc.setQueryData(['pins'], fresh)
        notify({ key: 'pin', message: 'Pinned to the top of the sidebar.' })
      },
    })
  }
  const pinSearch = async () => {
    setOpen(false)
    const name = await dialog.prompt({ title: 'Pin this search', label: 'Name', confirmLabel: 'Pin' })
    if (!name) return
    const url = toUrl({ query, smartId: null, item: null, full: false })
    pin({ kind: 'search', name, query: url.includes('?') ? url.slice(url.indexOf('?') + 1) : '' })
  }

  const freeProjects = (projects.data ?? []).filter((p) => !isPinned(pins, 'project', p.id)).slice(0, 8)
  const freeBoards = (boards.data ?? []).filter((b) => !isPinned(pins, 'board', b.id)).slice(0, 8)

  return (
    <div className="pj-pin-menu" ref={ref}>
      <button
        className="icon-btn"
        aria-label="Pin something"
        title="Pin this view, a search, a project or a board"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Plus size={14} aria-hidden="true" />
      </button>
      {open && (
        <div className="pj-menu float-surface" role="menu" aria-label="Pin">
          {target && !isPinned(pins, target.kind, target.ref) && (
            <button role="menuitem" onClick={() => pin(target)}>
              <PinIcon size={14} aria-hidden="true" /> Pin {targetName}
            </button>
          )}
          {target && isPinned(pins, target.kind, target.ref) && (
            <p className="pj-menu-note">{targetName} is pinned.</p>
          )}
          {searchable && (
            <button role="menuitem" onClick={() => void pinSearch()}>
              <Search size={14} aria-hidden="true" /> Pin this search…
            </button>
          )}
          {freeProjects.length > 0 && <div className="section-label pj-menu-label">Projects</div>}
          {freeProjects.map((p) => (
            <button key={p.id} role="menuitem" onClick={() => pin({ kind: 'project', ref: p.id })}>
              <Briefcase size={14} aria-hidden="true" /> {p.name}
            </button>
          ))}
          {freeBoards.length > 0 && <div className="section-label pj-menu-label">Boards</div>}
          {freeBoards.map((b) => (
            <button key={b.id} role="menuitem" onClick={() => pin({ kind: 'board', ref: b.id })}>
              <Presentation size={14} aria-hidden="true" /> {b.name}
            </button>
          ))}
          {!target && !searchable && freeProjects.length === 0 && freeBoards.length === 0 && (
            <p className="pj-menu-note">Open a folder, tag, person or smart folder, or filter the grid, to pin it.</p>
          )}
        </div>
      )}
    </div>
  )
}

/** A pin/unpin button for a sidebar row's actions (folders, smart folders). */
export function PinToggle({ kind, refId, name, labelled = false }: { kind: PinKind; refId: string; name: string; labelled?: boolean }) {
  const pins = usePins()
  const qc = useQueryClient()
  const w = useWrite()
  const on = isPinned(pins.data, kind, refId)
  return (
    <button
      className={labelled ? `btn-icon${on ? ' on' : ''}` : undefined}
      title={on ? 'Unpin' : 'Pin to the top of the sidebar'}
      aria-label={labelled ? undefined : on ? `Unpin ${name}` : `Pin ${name}`}
      aria-pressed={on}
      onClick={() =>
        void w.run(`pin:${kind}:${refId}`, () => (on ? projectsApi.unpin(kind, refId) : projectsApi.pin({ kind, ref: refId })), {
          what: on ? 'unpin it' : 'pin it',
          onDone: (fresh) => qc.setQueryData(['pins'], fresh),
        })
      }
    >
      {labelled ? <PinIcon size={14} aria-hidden="true" /> : on ? <PinOff size={14} aria-hidden="true" /> : <PinIcon size={14} aria-hidden="true" />}
      {labelled && (on ? 'Pinned' : 'Pin')}
    </button>
  )
}

/** "Projects" in the sidebar's Views list. */
export function ProjectsNavRow() {
  const { screen, openScreen } = useAppState()
  const on = screen?.kind === 'projects' || screen?.kind === 'project'
  return (
    <div className={`row ${on ? 'active' : ''}`}>
      <button
        className="row-main"
        aria-current={on ? 'page' : undefined}
        title="Jobs: their folders, boards, share links and custom fields"
        onClick={() => openScreen({ kind: 'projects' })}
      >
        <span className="icon" aria-hidden="true">
          <Briefcase {...ICON} />
        </span>
        <span className="name">Projects</span>
      </button>
    </div>
  )
}
