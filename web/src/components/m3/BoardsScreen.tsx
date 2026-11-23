// The boards list (/boards) with thumbnails and the boards trash, in the
// library's frame. Its own module so it loads on first use (App.tsx); the
// sidebar's boards and culling sections stay in Boards.tsx.

import { useQueryClient } from '@tanstack/react-query'
import { Copy, Pencil, Plus, Presentation, RotateCcw, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import { emptyQuery } from '../../api'
import type { BoardSummary } from '../../board'
import { api3 } from '../../creativeApi'
import { useCaps } from '../../data'
import { useAppState } from '../../state'
import { useWrite } from '../../useWrite'
import { describeError } from '../../writes'
import { useDialog } from '../Dialog'
import { useNotices } from '../Notices'
import { useBoards, useNewBoard } from './Boards'

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
        <h1 id="boards-h" className="view-title">{trash ? 'Boards trash' : 'Boards'}</h1>
        <span className="grow" />
        <button className="btn-icon" aria-pressed={trash} onClick={() => openScreen({ kind: 'boards', trash: !trash })}>
          <Trash2 size={14} aria-hidden="true" /> {trash ? 'Back to boards' : 'Trash'}
        </button>
        {caps.canEdit && !trash && !mobile && (
          <button className="btn-primary btn-icon" onClick={() => void newBoard()}>
            <Plus size={16} aria-hidden="true" /> New board
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
                <button className="btn-primary btn-icon" onClick={() => void newBoard()}>
                  <Plus size={16} aria-hidden="true" /> New board
                </button>
              )}
            </>
          )}
        </div>
      )}
      <ul className="board-cards">
        {(boards.data ?? []).map((b) => (
          <li
            key={b.id}
            className={`board-card${trash ? '' : ' opens'}`}
            // the whole card opens the board, except its action buttons;
            // the picture below is the button keyboards and screen readers use
            onClick={(e) => {
              const control = (e.target as HTMLElement).closest('button, a, input')
              if (trash || (control && !control.classList.contains('board-open'))) return
              openScreen({ kind: 'board', id: b.id })
            }}
          >
            {/* its click (mouse, Enter, Space) bubbles to the card's */}
            <button className="board-open" disabled={trash} aria-label={`${b.name}, ${b.item_count} items, edited ${when(b.modified_at)}`}>
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
                      <RotateCcw size={14} aria-hidden="true" /> Restore
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
        <Presentation size={28} />
      </span>
    )
  return <img className="board-thumb" src={b.thumb_url} alt="" loading="lazy" onError={() => setFailed(true)} />
}
