import { ChevronDown, Lock, LockOpen, MoreHorizontal, Trash2, X, type LucideIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { bulkReport, hardDeleteQuestion, trashedText, useBulkAction, type BulkAction } from '../bulk'
import { bulkDoneText } from '../bulkText'
import { useAfterUndo, useCaps, useFolderNames, useFolders, useRefresh } from '../data'
import { undoReport } from '../history'
import { describeError } from '../writes'
import { useAppState } from '../state'
import { useShortcut } from '../useShortcuts'
import { useDialog } from './Dialog'
import { useNotices } from './Notices'
import { useStackActions } from './BulkStack'
import { BulkBoardSelect, BulkFlags, useBulkCreativeMore } from './m3/BulkCreative'
import { useBulkM45More } from './m45/BulkM45'
import { useBulkCreditsMore } from './credits/Credits' // F4
import { useBulkProjectFields } from './projects/BulkFields'
import { usePresentBulkItem } from './present/PresentHost'

/** One entry in the bulk bar's More menu. */
export interface BulkMenuItem {
  key: string
  label: string
  icon: LucideIcon
  run: () => void
  disabled?: boolean
  title?: string
}

/** The bar's More menu: everything that isn't one of its everyday
 * actions. Opens upward (the bar sits at the bottom of the grid). */
function BulkMore({ items }: { items: BulkMenuItem[] }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    wrap.current?.querySelector<HTMLElement>('[role=menuitem]:not(:disabled)')?.focus()
    const close = (e: MouseEvent) => !wrap.current?.contains(e.target as Node) && setOpen(false)
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])
  if (!items.length) return null
  const onKey = (e: React.KeyboardEvent) => {
    if (!open) return
    const els = [...(wrap.current?.querySelectorAll<HTMLElement>('[role=menuitem]:not(:disabled)') ?? [])]
    const i = els.indexOf(document.activeElement as HTMLElement)
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
      btn.current?.focus()
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const d = e.key === 'ArrowDown' ? 1 : -1
      els[(i + d + els.length) % els.length]?.focus()
    } else if (e.key === 'Tab') setOpen(false)
  }
  return (
    <div className="menu-wrap bulk-more" ref={wrap} onKeyDown={onKey}>
      <button
        ref={btn}
        type="button"
        className="btn-icon"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title="Protect, stack, compare, rename, share, export…"
      >
        <MoreHorizontal size={16} aria-hidden="true" /> More
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div className="menu" role="menu" aria-label="More actions for the selection">
          {items.map((m) => (
            <button
              key={m.key}
              type="button"
              role="menuitem"
              tabIndex={-1}
              disabled={m.disabled}
              title={m.title}
              onClick={() => {
                setOpen(false)
                m.run()
              }}
            >
              <m.icon size={16} aria-hidden="true" />
              {m.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// actions that take the items out of this view: clear the selection after
const LEAVES_VIEW = new Set(['trash', 'restore', 'hardDelete'])

export function BulkBar() {
  const { query, selection, clearSelection, selectAll } = useAppState()
  const { data: folders } = useFolders()
  const bulk = useBulkAction()
  const dialog = useDialog()
  const { notify, dismiss } = useNotices()
  const refresh = useRefresh()
  const afterUndo = useAfterUndo()
  const caps = useCaps()
  const folderName = useFolderNames()
  const [tagText, setTagText] = useState('')

  const ids = useMemo(() => [...selection], [selection])

  const run = (action: BulkAction, targets: string[] = ids) => {
    if (bulk.isPending || targets.length === 0) return
    dismiss('bulk')
    bulk.mutate(
      { ids: targets, action },
      {
        onSuccess: (r) => {
          if (r.failed.length) {
            // keep only what failed selected; the notice says why, with
            // Retry when trying again could help (not for protected items)
            const failedIds = r.failed.map((f) => f.id)
            const retryIds = r.failed.filter((f) => f.retryable !== false).map((f) => f.id)
            selectAll(failedIds)
            const done = targets.filter((id) => !failedIds.includes(id))
            notify({
              key: 'bulk',
              tone: 'error',
              message: bulkReport(r),
              action: retryIds.length ? { label: 'Retry', run: () => run(action, retryIds) } : undefined,
              // the ones that did go to the Trash can still come back
              actions: action.kind === 'trash' && done.length ? [{ label: 'Undo', run: () => undoTrash(done, r.batch) }] : [],
            })
          } else if (LEAVES_VIEW.has(action.kind)) {
            clearSelection()
          }
          // every edit here is one batch, so its notice offers Undo (the
          // same as Recent changes)
          const text = r.ok > 0 && !r.failed.length ? bulkDoneText(action, r.ok, (id) => folderName.get(id)) : null
          if (text) notify({ key: 'bulk', message: text, action: { label: 'Undo', run: () => undoBatch(r.batch) } })
          // trashing is undoable: the whole action was one batch, so Undo
          // reverts that batch (and says so if something changed since)
          if (action.kind === 'trash' && r.ok > 0 && !r.failed.length) {
            notify({
              key: 'trash',
              message: trashedText(targets.length),
              action: { label: 'Undo', run: () => undoTrash(targets, r.batch) },
            })
          }
        },
      },
    )
  }

  const undoBatch = (batch: string) =>
    api.undoBatch(batch).then(
      (res) => {
        afterUndo()
        const rep = undoReport(res, 'Undid', { folder: (id) => folderName.get(id) })
        notify({ key: 'bulk', tone: rep.tone, message: rep.message })
      },
      (e) => notify({ key: 'bulk', tone: 'error', message: `Couldn't undo: ${describeError(e)}`, action: { label: 'Retry', run: () => void undoBatch(batch) } }),
    )

  // put trashed items back by undoing the trash batch, then reload the
  // list on screen so they show up where they sort. A server without batch
  // undo (older) gets the items restored one by one instead.
  const undoTrash = (trashed: string[], batch?: string) => {
    if (batch) {
      api.undoBatch(batch).then(
        (res) => {
          afterUndo()
          const rep = undoReport(res, 'Restored', { folder: (id) => folderName.get(id) })
          if (rep.tone === 'error') notify({ key: 'trash', tone: 'error', message: rep.message })
          else {
            dismiss('trash')
            selectAll(trashed)
          }
        },
        (e) => {
          if ((e as { status?: number })?.status === 404) restoreEach(trashed)
          else
            notify({
              key: 'trash',
              tone: 'error',
              message: `Couldn't undo: ${describeError(e)}`,
              action: { label: 'Retry', run: () => undoTrash(trashed, batch) },
            })
        },
      )
      return
    }
    restoreEach(trashed)
  }

  const restoreEach = (trashed: string[]) =>
    bulk.mutate(
      { ids: trashed, action: { kind: 'restore' } },
      {
        onSuccess: (r) => {
          refresh.items()
          if (r.failed.length)
            notify({
              key: 'trash',
              tone: 'error',
              message: bulkReport(r),
              action: r.failed.some((f) => f.retryable !== false)
                ? { label: 'Retry', run: () => restoreEach(r.failed.filter((f) => f.retryable !== false).map((f) => f.id)) }
                : undefined,
            })
          else selectAll(trashed)
        },
      },
    )

  const hardDelete = async () => {
    const n = ids.length
    const ok = await dialog.confirm({
      title: hardDeleteQuestion(n),
      confirmLabel: 'Delete forever',
      danger: true,
    })
    if (ok) run({ kind: 'hardDelete' })
  }

  // Delete key → trash (or delete forever in trash view). Grid layer only:
  // with the preview or a dialog open the key does nothing; and only with
  // focus on the grid (or nowhere), so Backspace on a button can't trash.
  useShortcut(
    'grid',
    (e) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return false
      const t = e.target as HTMLElement | null
      if (t && t !== document.body && !t.closest?.('.grid-scroll')) return false
      e.preventDefault()
      if (query.deleted) {
        if (caps.canDestroy) void hardDelete()
      } else run({ kind: 'trash' })
      return true
    },
    ids.length > 0 && caps.canEdit,
  )

  const busy = bulk.isPending
  const stackItems = useStackActions(busy)
  const creativeItems = useBulkCreativeMore(ids, busy)
  const m45Items = useBulkM45More(ids, busy)
  const creditItems = useBulkCreditsMore(ids, busy) // F4
  const projectFields = useBulkProjectFields(ids, busy) // F6
  const presentItem = usePresentBulkItem(ids, busy)

  // one item selected: the info panel has every action; the bar is for
  // two or more (B5). Delete still trashes a single selection (above).
  if (ids.length < 2 || !caps.canEdit) return null

  const addTags = () => {
    const tags = tagText.split(',').map((t) => t.trim()).filter(Boolean)
    if (tags.length) {
      run({ kind: 'addTags', tags })
      setTagText('')
    }
  }

  const moreItems: BulkMenuItem[] = [
    { key: 'protect', label: 'Protect', icon: Lock, disabled: busy, title: "Protected items can't be trashed or deleted until unprotected", run: () => run({ kind: 'protect', value: true }) },
    ...(caps.canDestroy
      ? [{ key: 'unprotect', label: 'Unprotect', icon: LockOpen, disabled: busy, run: () => run({ kind: 'protect', value: false }) }]
      : []),
    ...stackItems,
    ...creativeItems,
    ...m45Items,
    ...creditItems,
    ...(caps.restricted ? [] : projectFields.items),
    presentItem,
  ]

  // one 40 px row docked under the grid (B5): count and clear, then Tag,
  // Folder, Board, then Pick, Reject, Label, then More and Trash
  return (
    <div className="bulkbar" role="toolbar" aria-label="Selection actions">
      <span className="sel-count">{ids.length} selected</span>
      {projectFields.element}
      <button className="icon-btn" onClick={clearSelection} aria-label="Clear selection" title="Clear selection (Esc)">
        <X size={16} aria-hidden="true" />
      </button>

      {!query.deleted && (
        <>
          <span className="bulk-sep" aria-hidden="true" />
          <input
            className="bulk-tags"
            placeholder="Tag…"
            aria-label="Tags to add to the selected items, comma-separated; Enter adds them"
            title="Tags to add, comma-separated; Enter adds them"
            value={tagText}
            disabled={busy}
            onChange={(e) => setTagText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addTags()}
          />
          <select
            value=""
            aria-label="Add the selected items to a folder"
            disabled={busy}
            onChange={(e) => {
              if (e.target.value === '') return
              if (e.target.value === '__none__') run({ kind: 'move', folderId: null })
              else run({ kind: 'addFolder', folderId: e.target.value })
            }}
          >
            <option value="">Folder</option>
            <option value="__none__">(remove from all folders)</option>
            {(folders ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
          <BulkBoardSelect ids={ids} busy={busy} />
          <span className="bulk-sep" aria-hidden="true" />
          <BulkFlags ids={ids} busy={busy} />
          <span className="bulk-grow" />
          <BulkMore items={moreItems} />
          <button className="btn-icon danger" disabled={busy} onClick={() => run({ kind: 'trash' })}>
            <Trash2 size={16} aria-hidden="true" />
            Trash
          </button>
        </>
      )}

      {query.deleted && (
        <>
          <span className="bulk-grow" />
          <button disabled={busy} onClick={() => run({ kind: 'restore' })}>
            Restore
          </button>
          {caps.canDestroy && (
            <button className="danger" disabled={busy} onClick={() => void hardDelete()}>
              Delete forever
            </button>
          )}
        </>
      )}

      <span className="working" role="status">
        {busy ? 'Working…' : ''}
      </span>
    </div>
  )
}
