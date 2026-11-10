// Boards in the library: a sidebar section (open, new, drop items on a
// board), the /boards list with thumbnails (and the boards trash), and the
// culling filters (picks, rejects, labels).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Copy, Flag, LayoutDashboard, ListChecks, Pencil, Plus, RotateCcw, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import { emptyQuery } from '../../api'
import type { BoardSummary } from '../../board'
import { api3 } from '../../creativeApi'
import { LABELS } from '../../cull'
import { useCaps } from '../../data'
import { useAppState } from '../../state'
import { useWrite } from '../../useWrite'
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

export function SidebarBoards() {
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
      <div className="section-head" style={{ marginTop: 16 }}>
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
            <span className="name">Triage Unfiled</span>
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

const when = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })

export function BoardsScreen({ trash = false }: { trash?: boolean }) {
  const { openScreen, setQuery, mobile } = useAppState()
  const caps = useCaps()
  const boards = useBoards(trash ? 'trashed' : 'active')
  const newBoard = useNewBoard()
  const qc = useQueryClient()
  const dialog = useDialog()
  const w = useWrite()
  const { notify } = useNotices()
  const refresh = () => void qc.invalidateQueries({ queryKey: ['boards'] })

  const rename = async (b: BoardSummary) => {
    const name = await dialog.prompt({ title: 'Rename board', label: 'Name', initial: b.name, confirmLabel: 'Rename' })
    if (!name || name === b.name) return
    void w.run(`b:${b.id}`, () => api3.patchBoard(b.id, { name, add: [], update: [], remove: [], edges_add: [], edges_update: [], edges_remove: [] }), {
      what: 'rename the board',
      onDone: refresh,
    })
  }
  const remove = (b: BoardSummary) =>
    void w.run(`b:${b.id}`, () => api3.deleteBoard(b.id), {
      what: 'move the board to the trash',
      onDone: () => {
        refresh()
        notify({ key: 'board', message: `Moved “${b.name}” to the boards trash.`, action: { label: 'Undo', run: () => void api3.restoreBoard(b.id).then(refresh) } })
      },
    })
  const restore = (b: BoardSummary) => void w.run(`b:${b.id}`, () => api3.restoreBoard(b.id), { what: 'restore the board', onDone: refresh })
  const destroy = async (b: BoardSummary) => {
    const ok = await dialog.confirm({ title: `Delete “${b.name}” for good?`, message: 'The items on it stay in the library.', confirmLabel: 'Delete forever', danger: true })
    if (ok) void w.run(`b:${b.id}`, () => api3.deleteBoard(b.id, true), { what: 'delete the board', onDone: refresh })
  }
  const duplicate = (b: BoardSummary) =>
    void w.run(`b:${b.id}`, () => api3.duplicateBoard(b.id, `${b.name} copy`), { what: 'duplicate the board', onDone: refresh })

  return (
    <main className={`boards-screen${mobile ? ' mobile' : ''}`} aria-labelledby="boards-h">
      <div className="boards-head">
        {mobile && (
          <button className="icon-btn" onClick={() => setQuery({ ...emptyQuery })} aria-label="Back to the library">
            <X size={16} aria-hidden="true" />
          </button>
        )}
        <h1 id="boards-h">{trash ? 'Boards trash' : 'Boards'}</h1>
        <span className="grow" />
        <button className="btn-icon" aria-pressed={trash} onClick={() => openScreen({ kind: 'boards', trash: !trash })}>
          <Trash2 size={14} aria-hidden="true" /> {trash ? 'Back to boards' : 'Trash'}
        </button>
        {caps.canEdit && !trash && !mobile && (
          <button className="primary btn-icon" onClick={() => void newBoard()}>
            <Plus size={15} aria-hidden="true" /> New board
          </button>
        )}
      </div>
      {boards.isLoading && <p className="center">Loading…</p>}
      {boards.isError && <p className="error" role="alert">Couldn’t load the boards: {describeError(boards.error)}</p>}
      {boards.data?.length === 0 && (
        <div className="boards-empty">
          {trash ? (
            <p>The boards trash is empty.</p>
          ) : (
            <>
              <h2>Boards are moodboards made from your library</h2>
              <p>Lay items out on a canvas with notes, colour swatches, arrows and groups, then export it as PNG, PDF or one HTML file.</p>
              <p>Start one here, or select items in the library and choose Board in the bar at the bottom.</p>
              {caps.canEdit && (
                <button className="primary btn-icon" onClick={() => void newBoard()}>
                  <Plus size={15} aria-hidden="true" /> New board
                </button>
              )}
            </>
          )}
        </div>
      )}
      <ul className="board-cards">
        {(boards.data ?? []).map((b) => (
          <li key={b.id} className="board-card">
            <button className="board-open" onClick={() => !trash && openScreen({ kind: 'board', id: b.id })} disabled={trash} aria-label={`${b.name}, ${b.item_count} items, edited ${when(b.modified_at)}`}>
              <BoardThumb b={b} />
            </button>
            <div className="board-card-text">
              <h2 className="board-name">{b.name}</h2>
              <p className="hint">
                {b.item_count} item{b.item_count === 1 ? '' : 's'} · {when(b.modified_at)}
              </p>
            </div>
            {caps.canEdit && (
              <div className="board-card-actions">
                {trash ? (
                  <>
                    <button className="btn-icon small" onClick={() => restore(b)}>
                      <RotateCcw size={13} aria-hidden="true" /> Restore
                    </button>
                    {caps.canDestroy && (
                      <button className="btn-icon small danger" onClick={() => void destroy(b)}>
                        Delete forever
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    <button className="icon-btn" aria-label={`Rename ${b.name}`} title="Rename" onClick={() => void rename(b)}>
                      <Pencil size={14} aria-hidden="true" />
                    </button>
                    <button className="icon-btn" aria-label={`Duplicate ${b.name}`} title="Duplicate" onClick={() => duplicate(b)}>
                      <Copy size={14} aria-hidden="true" />
                    </button>
                    <button className="icon-btn" aria-label={`Move ${b.name} to the trash`} title="Move to trash" onClick={() => remove(b)}>
                      <Trash2 size={14} aria-hidden="true" />
                    </button>
                  </>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </main>
  )
}

function BoardThumb({ b }: { b: BoardSummary }) {
  const [failed, setFailed] = useState(false)
  if (failed)
    return (
      <span className="board-thumb empty" aria-hidden="true">
        <LayoutDashboard size={28} strokeWidth={1.25} />
      </span>
    )
  return <img className="board-thumb" src={b.thumb_url} alt="" loading="lazy" onError={() => setFailed(true)} />
}
