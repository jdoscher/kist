// The bulk bar's Milestone 3 controls: flag and label the selection, put
// it on a board (in the bar), and compare, survey, rename with a pattern
// or apply a template (in its More menu, B5).

import { useQueryClient } from '@tanstack/react-query'
import { Ban, Columns2, Flag, Grid2x2, PencilLine, Stamp } from 'lucide-react'
import { api } from '../../api'
import { bulkReport, useBulkAction, type BulkAction } from '../../bulk'
import { bulkDoneText } from '../../bulkText'
import { useAfterUndo } from '../../data'
import { undoReport } from '../../history'
import { api3 } from '../../creativeApi'
import { LABELS } from '../../cull'
import { describeError } from '../../writes'
import { useAppState } from '../../state'
import { useNotices } from '../Notices'
import type { BulkMenuItem } from '../BulkBar'
import { useBoards, useNewBoard } from './Boards'
import { COMPARE_MAX, useCreative } from './Creative'

/** Run a flag or label change on the selection, with an Undo notice. */
function useFlagRun(ids: string[]) {
  const bulk = useBulkAction()
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  const run = (action: BulkAction) =>
    bulk.mutate(
      { ids, action },
      {
        onSuccess: (r) => {
          if (r.failed.length) return notify({ key: 'bulk', tone: 'error', message: bulkReport(r) })
          const text = r.ok ? bulkDoneText(action, r.ok) : null
          if (text)
            notify({
              key: 'bulk',
              message: text,
              action: {
                label: 'Undo',
                run: () =>
                  void api.undoBatch(r.batch).then(
                    (res) => {
                      afterUndo()
                      const rep = undoReport(res)
                      notify({ key: 'bulk', tone: rep.tone, message: rep.message })
                    },
                    (e) => notify({ key: 'bulk', tone: 'error', message: `Couldn't undo: ${describeError(e)}` }),
                  ),
              },
            })
        },
      },
    )
  return { run, pending: bulk.isPending }
}

/** Pick, Reject and the Label menu (which also unflags). */
export function BulkFlags({ ids, busy }: { ids: string[]; busy: boolean }) {
  const { run, pending } = useFlagRun(ids)
  const disabled = busy || pending
  return (
    <>
      <button className="btn-icon" disabled={disabled} onClick={() => run({ kind: 'flag', flag: 'pick' })} title="Pick them all">
        <Flag size={14} aria-hidden="true" /> Pick
      </button>
      <button className="btn-icon" disabled={disabled} onClick={() => run({ kind: 'flag', flag: 'reject' })} title="Reject them all">
        <Ban size={14} aria-hidden="true" /> Reject
      </button>
      <select
        value=""
        aria-label="Flag or label the selected items"
        disabled={disabled}
        onChange={(e) => {
          const v = e.target.value
          if (v === 'unflag') run({ kind: 'flag', flag: null })
          else if (v === 'nolabel') run({ kind: 'label', label: null })
          else if (v) run({ kind: 'label', label: v as BulkActionLabel })
        }}
      >
        <option value="">Label</option>
        {LABELS.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name}
          </option>
        ))}
        <option value="nolabel">(no label)</option>
        <option value="unflag">(unflag)</option>
      </select>
    </>
  )
}

/** Put the selection on a board (or a new one). */
export function BulkBoardSelect({ ids, busy }: { ids: string[]; busy: boolean }) {
  const { notify } = useNotices()
  const { openScreen } = useAppState()
  const boards = useBoards()
  const newBoard = useNewBoard()
  const qc = useQueryClient()
  const toBoard = async (value: string) => {
    if (value === '__new__') return void newBoard(ids)
    const b = boards.data?.find((x) => x.id === value)
    if (!b) return
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
    <select value="" aria-label="Put the selected items on a board" disabled={busy} onChange={(e) => e.target.value && void toBoard(e.target.value)}>
      <option value="">Board</option>
      <option value="__new__">New board with these…</option>
      {(boards.data ?? []).map((b) => (
        <option key={b.id} value={b.id}>
          {b.name}
        </option>
      ))}
    </select>
  )
}

/** The More menu's Milestone 3 entries: compare, survey, rename, template. */
export function useBulkCreativeMore(ids: string[], busy: boolean): BulkMenuItem[] {
  const creative = useCreative()
  return [
    {
      key: 'compare',
      label: 'Compare',
      icon: Columns2,
      disabled: ids.length > COMPARE_MAX,
      title: ids.length > COMPARE_MAX ? `Compare takes 2 to ${COMPARE_MAX}; Survey takes more` : 'Side by side, wipe or overlay (C)',
      run: () => creative.openCompare(ids, 'compare'),
    },
    { key: 'survey', label: 'Survey', icon: Grid2x2, title: 'Pick and reject one by one (N)', run: () => creative.openCompare(ids, 'survey') },
    { key: 'rename', label: 'Rename…', icon: PencilLine, disabled: busy, run: () => creative.openRename(ids) },
    { key: 'template', label: 'Apply a template…', icon: Stamp, disabled: busy, run: () => creative.openTemplate(ids) },
  ]
}

type BulkActionLabel = Extract<BulkAction, { kind: 'label' }>['label']
