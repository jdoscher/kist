// Present mode (Kist 2.0 F2): where a show starts. PresentProvider holds
// the open request (the setup dialog) and the running show; the buttons
// that start one live here too (top bar, phone menu, selection bar, board).
// Small on purpose: the setup dialog and the show itself load on first use.

import { Presentation } from 'lucide-react'
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { itemsParams, type Query } from '../../api'
import { useFolderNames, useSmartFolders } from '../../data'
import type { PresentRequest, Resolved, ShowOptions } from '../../present'
import { useAppState } from '../../state'
import type { BulkMenuItem } from '../BulkBar'
import '../../present.css'

const PresentSetup = lazy(() => import('./PresentSetup'))
const Show = lazy(() => import('./Show'))

// a saved show opened by link (/?present=<id>, the MCP tools' web_url):
// read once, before the app's URL sync rewrites the address
const LINKED_SHOW = (() => {
  try {
    return new URLSearchParams(window.location.search).get('present')
  } catch {
    return null
  }
})()

interface Running {
  resolved: Resolved
  options: ShowOptions
  /** keep the images offline, within this many bytes (0 = no) */
  offlineCap: number
  /** where it came from (for Save show from inside the show) */
  request: PresentRequest
}

interface PresentApi {
  openPresent: (req: PresentRequest) => void
  startShow: (run: Running) => void
}

const Ctx = createContext<PresentApi | null>(null)

export function usePresent(): PresentApi {
  const c = useContext(Ctx)
  if (!c) throw new Error('usePresent outside PresentProvider')
  return c
}

export function PresentProvider({ children }: { children: ReactNode }) {
  const [req, setReq] = useState<PresentRequest | null>(() =>
    LINKED_SHOW && /^[0-9A-HJKMNP-TV-Z]{26}$/.test(LINKED_SHOW) ? { name: 'Show', source: { kind: 'items', item_ids: [] }, showId: LINKED_SHOW } : null,
  )
  const [running, setRunning] = useState<Running | null>(null)
  useEffect(() => {
    if (!LINKED_SHOW) return
    const url = new URL(window.location.href)
    url.searchParams.delete('present')
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
  }, [])
  const api = useMemo<PresentApi>(
    () => ({
      openPresent: (r) => setReq(r),
      startShow: (run) => {
        setReq(null)
        setRunning(run)
      },
    }),
    [],
  )
  const close = useCallback(() => setRunning(null), [])
  return (
    <Ctx.Provider value={api}>
      {children}
      <Suspense fallback={null}>
        {req && !running && <PresentSetup request={req} onClose={() => setReq(null)} />}
        {running && <Show key={running.resolved.show_id ?? running.resolved.name} {...running} onClose={close} />}
      </Suspense>
    </Ctx.Provider>
  )
}

// ---------- what's on screen, as a show ----------

/** Filters that make a view more than "this folder". */
function onlyFolder(q: Query): boolean {
  return (
    !q.q && !q.tags.length && !q.tags_any?.length && !q.untagged && !q.view && !q.preview && !q.fixity && !q.type && !q.rating &&
    !q.deleted && !q.created_after && !q.created_before && !q.semantic && !q.similar && !q.color && !q.text && !q.person &&
    !q.flag && !q.label && !q.image
  )
}

/** The grid's view as a Present request: it plays exactly what the grid
 * shows (its own query string, in its order), and saves as the live
 * folder or smart folder when the view is just that. */
export function viewRequest(query: Query, smartId: string | null, names: { folder?: string; smart?: string }): PresentRequest {
  const params = itemsParams(query).toString()
  if (smartId) return { name: names.smart ?? 'Smart folder', source: { kind: 'search' }, params, saveAs: { kind: 'smart', id: smartId } }
  if (query.folder && query.folder !== 'none') {
    return {
      name: names.folder ?? 'Folder',
      source: { kind: 'search' },
      params,
      saveAs: onlyFolder(query) ? { kind: 'folder', id: query.folder } : undefined,
    }
  }
  const name = query.q || query.semantic || (query.tags.length ? query.tags.join(', ') : '') || 'Library'
  return { name, source: { kind: 'search' }, params }
}

/** Present what's on screen (top bar; the phone menu). */
export function PresentButton({ label = false, onDone }: { label?: boolean; onDone?: () => void }) {
  const { query, smartId, selection } = useAppState()
  const { openPresent } = usePresent()
  const folderName = useFolderNames()
  const { data: smart } = useSmartFolders()
  const disabled = !!query.image || !!query.deleted
  const sel = [...selection]
  const run = () => {
    onDone?.()
    if (sel.length > 1) {
      openPresent({ name: `${sel.length} items`, source: { kind: 'items', item_ids: sel } })
      return
    }
    openPresent(
      viewRequest(query, smartId, {
        folder: query.folder ? folderName.get(query.folder) : undefined,
        smart: smartId ? smart?.find((s) => s.id === smartId)?.name : undefined,
      }),
    )
  }
  const title = disabled
    ? 'Present works on the library, a folder, a smart folder, a board, a search or a selection'
    : sel.length > 1
      ? `Present the ${sel.length} selected items full screen`
      : 'Present this view full screen (a slideshow with presenter view and phone remote)'
  return (
    <button
      type="button"
      className={label ? 'btn-icon present-open-btn' : 'icon-btn topbar-icon'}
      onClick={run}
      disabled={disabled}
      aria-label={label ? undefined : 'Present'}
      title={title}
    >
      <Presentation size={16} aria-hidden="true" />
      {label && <span>Present</span>}
    </button>
  )
}

/** The selection bar's More menu: present the selection. */
export function usePresentBulkItem(ids: string[], busy: boolean): BulkMenuItem {
  const { openPresent } = usePresent()
  return {
    key: 'present',
    label: 'Present…',
    icon: Presentation,
    disabled: busy || ids.length === 0,
    title: 'A full-screen show of the selection, in the order you picked them',
    run: () => openPresent({ name: `${ids.length} items`, source: { kind: 'items', item_ids: ids } }),
  }
}

/** A board's toolbar: present the board in board order. */
export function PresentBoardButton({ id, name }: { id: string; name: string }) {
  const { openPresent } = usePresent()
  return (
    <button
      className="icon-btn"
      aria-label="Present this board"
      title="Present this board full screen, in board order"
      onClick={() => openPresent({ name, source: { kind: 'board', id }, saveAs: { kind: 'board', id } })}
    >
      <Presentation size={16} aria-hidden="true" />
    </button>
  )
}

export type { Running }
