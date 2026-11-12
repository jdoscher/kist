// Boards in the library: a sidebar section (open, new, drop items on a
// board) and the culling filters (picks, rejects, labels). The /boards list
// is BoardsScreen.tsx (loaded on first use).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Flag, LayoutDashboard, ListChecks, Plus, X } from 'lucide-react'
import { useState } from 'react'
import type { BoardSummary } from '../../board'
import { api3 } from '../../creativeApi'
import { LABELS } from '../../cull'
import { useCaps } from '../../data'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { useDialog } from '../Dialog'
import { useNotices } from '../Notices'

export function useBoards(state: 'active' | 'trashed' = 'active') {
  return useQuery({ queryKey: ['boards', state], queryFn: () => api3.listBoards(state), staleTime: 15_000 })
}

/** Ask for a name, create the board (with these items), open it. */
export function useNewBoard() {
  const dialog = useDialog()
  const { openScreen } = useAppState()
  const qc = useQueryClient()
  const { notify } = useNotices()
  return async (itemIds: string[] = []) => {
    const name = await dialog.prompt({ title: 'New board', label: 'Name', initial: 'Untitled board', confirmLabel: 'Create' })
    if (!name) return
    try {
      const b = await api3.createBoard({ name, item_ids: itemIds })
      void qc.invalidateQueries({ queryKey: ['boards'] })
      openScreen({ kind: 'board', id: b.id })
    } catch (e) {
      notify({ key: 'board', tone: 'error', message: `Couldn't create the board: ${describeError(e)}` })
    }
  }
}

const ICON = { size: 15, strokeWidth: 1.75 } as const

export function SidebarBoards({ first = false }: { first?: boolean } = {}) {
  const { screen, openScreen } = useAppState()
  const caps = useCaps()
  const boards = useBoards()
  const newBoard = useNewBoard()
  const qc = useQueryClient()
  const { notify } = useNotices()
  const [over, setOver] = useState<string | null>(null)
  const list = boards.data ?? []
  const shown = list.slice(0, 8)

  const drop = async (b: BoardSummary, ids: string[]) => {
    try {
      await api3.addToBoard(b.id, ids)
      void qc.invalidateQueries({ queryKey: ['boards'] })
      void qc.invalidateQueries({ queryKey: ['board', b.id] })
      notify({ key: 'board', message: `Added ${ids.length} to “${b.name}”.`, action: { label: 'Open board', run: () => openScreen({ kind: 'board', id: b.id }) } })
    } catch (e) {
      notify({ key: 'board', tone: 'error', message: `Couldn't add to the board: ${describeError(e)}` })
    }
  }

  return (
    <>
      <div className="section-head" style={first ? undefined : { marginTop: 16 }}>
        <h2>Boards</h2>
        {caps.canEdit && (
          <button className="icon-btn" onClick={() => void newBoard()} title="New board" aria-label="New board">
            <Plus size={14} strokeWidth={2} aria-hidden="true" />
          </button>
        )}
      </div>
      {shown.map((b) => {
        const active = screen?.kind === 'board' && screen.id === b.id
        return (
          <div
            key={b.id}
            className={`row board-row${active ? ' active' : ''}${over === b.id ? ' drag-over' : ''}`}
            onDragOver={(e) => {
              if (caps.canEdit && e.dataTransfer.types.includes('application/x-kist-items')) {
                e.preventDefault()
                setOver(b.id)
              }
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => {
              e.preventDefault()
              setOver(null)
              const raw = e.dataTransfer.getData('application/x-kist-items')
              if (raw) void drop(b, JSON.parse(raw))
            }}
          >
            <button className="row-main" aria-current={active ? 'page' : undefined} onClick={() => openScreen({ kind: 'board', id: b.id })} title="Open the board (drop items here to add them)">
              <span className="icon" aria-hidden="true">
                <LayoutDashboard {...ICON} />
              </span>
              <span className="name">{b.name}</span>
              {b.item_count > 0 && (
                <span className="count">
                  <span className="sr-only">, </span>
                  {b.item_count}
                  <span className="sr-only"> items</span>
                </span>
              )}
            </button>
          </div>
        )
      })}
      <div className="row">
        <button className="row-main" aria-current={screen?.kind === 'boards' ? 'page' : undefined} onClick={() => openScreen({ kind: 'boards' })}>
          <span className="icon" aria-hidden="true">
            <LayoutDashboard {...ICON} />
          </span>
          <span className="name">{list.length > shown.length ? `All ${list.length} boards` : 'All boards'}</span>
        </button>
      </div>
    </>
  )
}

/** Picks, rejects, unflagged and the colour labels, as view filters. */
export function SidebarCull() {
  const { query, setQuery, openScreen, screen } = useAppState()
  const caps = useCaps()
  const flagRow = (value: string, name: string, icon: React.ReactNode) => {
    const active = query.flag === value && !screen
    return (
      <div className={`row${active ? ' active' : ''}`} key={value}>
        <button className="row-main" aria-pressed={active} onClick={() => setQuery({ flag: active ? null : value, deleted: false })}>
          <span className="icon" aria-hidden="true">
            {icon}
          </span>
          <span className="name">{name}</span>
        </button>
      </div>
    )
  }
  return (
    <>
      <div className="section-head" style={{ marginTop: 16 }}>
        <h2>Cull</h2>
      </div>
      {caps.canEdit && (
        <div className="row">
          <button className="row-main" aria-current={screen?.kind === 'triage' ? 'page' : undefined} onClick={() => openScreen({ kind: 'triage', view: 'unfiled' })} title="Go through Unfiled one item at a time">
            <span className="icon" aria-hidden="true">
              <ListChecks {...ICON} />
            </span>
            <span className="name">Triage unfiled</span>
          </button>
        </div>
      )}
      {flagRow('pick', 'Picks', <Flag {...ICON} />)}
      {flagRow('reject', 'Rejects', <Ban {...ICON} />)}
      <div className="label-filter" role="group" aria-label="Filter by colour label">
        {LABELS.map((l) => {
          const active = query.label === l.id && !screen
          return (
            <button
              key={l.id}
              className={`label-swatch${active ? ' on' : ''}`}
              style={{ ['--swatch' as string]: l.hex }}
              aria-pressed={active}
              aria-label={`${l.name} label`}
              title={`Show ${l.name.toLowerCase()} label`}
              onClick={() => setQuery({ label: active ? null : l.id, deleted: false })}
            />
          )
        })}
        {query.label && (
          <button className="icon-btn" aria-label="Clear the label filter" title="Clear" onClick={() => setQuery({ label: null })}>
            <X size={13} aria-hidden="true" />
          </button>
        )}
      </div>
    </>
  )
}
