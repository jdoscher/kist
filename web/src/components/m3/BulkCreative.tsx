// The bulk bar's Milestone 3 controls: flag and label the selection, put
// it on a board, compare or survey it, rename it with a pattern, apply a
// template.

import { useQueryClient } from '@tanstack/react-query'
import { Ban, Check, Columns2, Grid2x2, PencilLine, Sparkles } from 'lucide-react'
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
import { useBoards, useNewBoard } from './Boards'
import { COMPARE_MAX, useCreative } from './Creative'

export function BulkCreative({ ids, busy }: { ids: string[]; busy: boolean }) {
  const bulk = useBulkAction()
  const creative = useCreative()
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  const { openScreen } = useAppState()
  const boards = useBoards()
  const newBoard = useNewBoard()
  const qc = useQueryClient()
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
  const disabled = busy || bulk.isPending
  return (
    <>
      <span className="bulk-sep" aria-hidden="true" />
      <button className="btn-icon" disabled={disabled} onClick={() => run({ kind: 'flag', flag: 'pick' })} title="Pick them all">
        <Check size={14} strokeWidth={2.25} aria-hidden="true" /> Pick
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
        <option value="">Label…</option>
        {LABELS.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name}
          </option>
        ))}
        <option value="nolabel">(no label)</option>
        <option value="unflag">(unflag)</option>
      </select>
      <select value="" aria-label="Put the selected items on a board" disabled={disabled} onChange={(e) => e.target.value && void toBoard(e.target.value)}>
        <option value="">Board…</option>
        <option value="__new__">New board with these…</option>
        {(boards.data ?? []).map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </select>
      {ids.length >= 2 && (
        <>
          <button className="btn-icon" disabled={ids.length > COMPARE_MAX} onClick={() => creative.openCompare(ids, 'compare')} title={ids.length > COMPARE_MAX ? `Compare takes 2 to ${COMPARE_MAX}; Survey takes more` : 'Side by side, wipe or overlay (C)'}>
            <Columns2 size={14} aria-hidden="true" /> Compare
          </button>
          <button className="btn-icon" onClick={() => creative.openCompare(ids, 'survey')} title="Pick and reject one by one (N)">
            <Grid2x2 size={14} aria-hidden="true" /> Survey
          </button>
        </>
      )}
      <button className="btn-icon" disabled={disabled} onClick={() => creative.openRename(ids)}>
        <PencilLine size={14} aria-hidden="true" /> Rename
      </button>
      <button className="btn-icon" disabled={disabled} onClick={() => creative.openTemplate(ids)}>
        <Sparkles size={14} aria-hidden="true" /> Template
      </button>
    </>
  )
}

type BulkActionLabel = Extract<BulkAction, { kind: 'label' }>['label']
