// The board editor (/board/:id): state, saving and the toolbar. The canvas
// (BoardCanvas), the side panels (BoardInspector, BoardLibrary) and the
// list view (BoardList, the non-dragging way to do everything: WCAG 2.5.7)
// all work on one Editor object.
//
// Saving: every change is diffed against what the server has and sent as a
// PATCH with base_version, a moment after you stop. If the board changed
// elsewhere (another tab, an agent) the server says 409 and a banner offers
// to combine, keep theirs or overwrite. Undo/redo here is per session;
// the saves are one batch per visit, so Recent changes can undo the lot.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Download,
  Grid3x3,
  Hand,
  Images,
  LayoutList,
  Link2,
  Maximize,
  MousePointer2,
  Palette,
  Redo2,
  Share2,
  Spline,
  Square,
  StickyNote,
  Type,
  Undo2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { newBatchId, type Item } from '../../../api'
import {
  boundsOf,
  CANVAS_BGS,
  clampZoom,
  contentOf,
  diffContent,
  applyPatch,
  fitRect,
  isEmptyPatch,
  newNode,
  pushHistory,
  redo as redoH,
  topZ,
  undo as undoH,
  type BoardContent,
  type BoardDoc,
  type History,
  type NodeType,
  type Viewport,
} from '../../../board'
import { api3, conflictBoard } from '../../../creativeApi'
import { useCaps } from '../../../data'
import { useAppState } from '../../../state'
import { describeError } from '../../../writes'
import { BoardCanvas } from './BoardCanvas'
import { BoardInspector } from './BoardInspector'
import { BoardLibrary } from './BoardLibrary'
import { BoardList } from './BoardList'
import { useBoardKeys } from './keys'
import { useM45 } from '../../m45/M45'
import { PhoneBoardMenu } from './PhoneBoardMenu'

export type Tool = 'select' | 'hand' | 'connect'
type SaveState = 'saved' | 'unsaved' | 'saving' | 'error' | 'conflict'

export interface Editor {
  id: string
  content: BoardContent
  /** a change in progress (a drag): shown, not yet an undo step */
  setLive: (c: BoardContent) => void
  /** a finished change: an undo step (from `before`, default what was shown before the live change) */
  commit: (c: BoardContent, before?: BoardContent) => void
  sel: string[]
  setSel: (ids: string[]) => void
  edge: string | null
  setEdge: (id: string | null) => void
  items: Map<string, Item>
  addItems: (items: Item[]) => void
  missing: Set<string>
  vp: Viewport
  setVp: (v: Viewport) => void
  tool: Tool
  setTool: (t: Tool) => void
  grid: number
  canEdit: boolean
  mobile: boolean
  viewSize: () => { w: number; h: number }
  /** place a new node of a type at the middle of the view */
  add: (type: NodeType, extra?: Parameters<typeof newNode>[4]) => void
  undo: () => void
  redo: () => void
  canUndo: boolean
  canRedo: boolean
  announce: (text: string) => void
  editingText: string | null
  setEditingText: (id: string | null) => void
  cropFor: string | null
  setCropFor: (id: string | null) => void
  fit: () => void
}

const SNAP = 20
const SAVE_DELAY = 700

export default function BoardEditor({ id }: { id: string }) {
  const { mobile, openScreen } = useAppState()
  const caps = useCaps()
  const qc = useQueryClient()
  const m45 = useM45()
  const canEdit = caps.canEdit && !mobile
  // re-read now and then: an agent or another tab may have changed it (adopted only while nothing here is unsaved)
  const board = useQuery({ queryKey: ['board', id], queryFn: () => api3.getBoard(id), refetchOnWindowFocus: true, refetchInterval: 15_000, retry: 1 })

  const [content, setContent] = useState<BoardContent | null>(null)
  const [history, setHistory] = useState<History>({ past: [], future: [] })
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [saveError, setSaveError] = useState('')
  const [conflict, setConflict] = useState<BoardDoc | null>(null)
  const [sel, setSelRaw] = useState<string[]>([])
  const [edge, setEdge] = useState<string | null>(null)
  const [vp, setVp] = useState<Viewport>({ x: 40, y: 40, zoom: 1 })
  const [tool, setTool] = useState<Tool>('select')
  const [view, setView] = useState<'canvas' | 'list'>('canvas')
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [editingText, setEditingText] = useState<string | null>(null)
  const [cropFor, setCropFor] = useState<string | null>(null)
  const [said, setSaid] = useState('')
  const [extraItems, setExtraItems] = useState<Map<string, Item>>(new Map())
  const viewport = useRef<HTMLDivElement>(null)

  // what the server has (at baseVersion), and the content shown before a live change
  const saved = useRef<BoardContent | null>(null)
  const base = useRef(0)
  const shown = useRef<BoardContent | null>(null)
  const committed = useRef<BoardContent | null>(null)
  const inflight = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const batch = useRef(newBatchId())
  shown.current = content

  // adopt the server copy: on open, and when it changed elsewhere while
  // nothing here is unsaved
  useEffect(() => {
    const d = board.data
    if (!d) return
    const dirty = saved.current && committed.current && !isEmptyPatch(diffContent(saved.current, committed.current))
    if (saved.current && (d.version <= base.current || dirty || inflight.current)) return
    const c = contentOf(d)
    const first = !saved.current
    saved.current = c
    committed.current = c
    base.current = d.version
    setContent(c)
    if (first) {
      setHistory({ past: [], future: [] })
      requestAnimationFrame(() => fitTo(c))
    } else setSaid('Updated: this board changed elsewhere.')
  }, [board.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const viewSize = useCallback(() => {
    const r = viewport.current?.getBoundingClientRect()
    return { w: r?.width ?? 800, h: r?.height ?? 600 }
  }, [])
  const fitTo = (c: BoardContent | null) => {
    const { w, h } = viewSize()
    setVp(fitRect(boundsOf(c?.nodes ?? []), w, h, 48, 1))
  }

  // ---------- saving ----------

  const save = useCallback(async () => {
    if (inflight.current || !saved.current || !committed.current) return
    if (timer.current) clearTimeout(timer.current)
    const snapshot = committed.current
    const patch = diffContent(saved.current, snapshot)
    if (isEmptyPatch(patch)) {
      setSaveState('saved')
      return
    }
    inflight.current = true
    setSaveState('saving')
    try {
      const doc = await api3.patchBoard(id, { ...patch, base_version: base.current }, batch.current)
      saved.current = snapshot
      base.current = doc.version
      qc.setQueryData(['board', id], doc)
      void qc.invalidateQueries({ queryKey: ['boards'] })
      void qc.invalidateQueries({ queryKey: ['batches'] })
      setSaveError('')
      inflight.current = false
      if (committed.current !== snapshot) void save()
      else setSaveState('saved')
    } catch (e) {
      inflight.current = false
      const theirs = conflictBoard(e)
      if (theirs) {
        setConflict(theirs)
        setSaveState('conflict')
      } else {
        setSaveError(describeError(e))
        setSaveState('error')
      }
    }
  }, [id, qc])

  const schedule = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    setSaveState((s) => (s === 'conflict' ? s : 'unsaved'))
    timer.current = setTimeout(() => {
      if (!conflictRef.current) void save()
    }, SAVE_DELAY)
  }, [save])
  const conflictRef = useRef<BoardDoc | null>(null)
  conflictRef.current = conflict

  // leaving: send what's left; closing the tab with unsaved edits asks first
  useEffect(() => {
    const onUnload = (e: BeforeUnloadEvent) => {
      if (saved.current && committed.current && !isEmptyPatch(diffContent(saved.current, committed.current))) e.preventDefault()
    }
    window.addEventListener('beforeunload', onUnload)
    return () => {
      window.removeEventListener('beforeunload', onUnload)
      if (!conflictRef.current) void save()
    }
  }, [save])

  // ---------- editing ----------

  const setLive = useCallback((c: BoardContent) => setContent(c), [])
  const commit = useCallback(
    (c: BoardContent, before?: BoardContent) => {
      const prev = before ?? committed.current
      if (prev) setHistory((h) => pushHistory(h, prev))
      committed.current = c
      setContent(c)
      schedule()
    },
    [schedule],
  )
  const undo = () => {
    if (!content) return
    const u = undoH(history, committed.current ?? content)
    if (!u) return
    setHistory(u.history)
    committed.current = u.content
    setContent(u.content)
    setSaid('Undone')
    schedule()
  }
  const redo = () => {
    if (!content) return
    const r = redoH(history, committed.current ?? content)
    if (!r) return
    setHistory(r.history)
    committed.current = r.content
    setContent(r.content)
    setSaid('Redone')
    schedule()
  }

  const setSel = useCallback((ids: string[]) => {
    setSelRaw(ids)
    if (ids.length) setEdge(null)
  }, [])

  // ---------- items on the board ----------

  const itemIds = useMemo(
    () => [...new Set((content?.nodes ?? []).filter((n) => n.type === 'item' && n.item_id).map((n) => n.item_id as string))].sort(),
    [content?.nodes],
  )
  const fetchIds = itemIds.filter((i) => !extraItems.has(i))
  const itemsQ = useQuery({
    queryKey: ['boardItems', fetchIds],
    queryFn: () => api3.itemsByIds(fetchIds),
    enabled: fetchIds.length > 0,
    staleTime: 60_000,
    placeholderData: (p) => p,
  })
  const items = useMemo(() => {
    const m = new Map(extraItems)
    for (const it of itemsQ.data?.items ?? []) m.set(it.id, it)
    return m
  }, [extraItems, itemsQ.data])
  const missing = useMemo(() => {
    const m = new Set(board.data?.missing_item_ids ?? [])
    if (itemsQ.isSuccess) for (const i of fetchIds) if (!items.has(i)) m.add(i)
    return m
  }, [board.data, itemsQ.isSuccess, fetchIds, items])
  const addItems = useCallback((list: Item[]) => setExtraItems((m) => new Map([...m, ...list.map((i) => [i.id, i] as const)])), [])

  const add = (type: NodeType, extra = {}) => {
    if (!content) return
    const { w, h } = viewSize()
    const cx = (w / 2 - vp.x) / vp.zoom
    const cy = (h / 2 - vp.y) / vp.zoom
    const n = newNode(type, 0, 0, topZ(content.nodes) + 1, extra)
    n.x = Math.round(cx - n.w / 2)
    n.y = Math.round(cy - n.h / 2)
    if (grid) {
      n.x = Math.round(n.x / grid) * grid
      n.y = Math.round(n.y / grid) * grid
    }
    // don't stack exactly on top of the last thing added here
    const step = grid || 24
    for (let i = 0; i < 40 && content.nodes.some((o) => Math.abs(o.x - n.x) < 8 && Math.abs(o.y - n.y) < 8); i++) {
      n.x += step
      n.y += step
    }
    commit({ ...content, nodes: [...content.nodes, n] })
    setSel([n.id])
    if (type === 'note' || type === 'text') setEditingText(n.id)
    setSaid(`Added a ${type === 'color' ? 'colour swatch' : type}`)
  }

  const grid = content?.canvas.snap === false ? 0 : (content?.canvas.grid ?? 0)

  const ed: Editor | null = content
    ? {
        id,
        content,
        setLive,
        commit,
        sel,
        setSel,
        edge,
        setEdge,
        items,
        addItems,
        missing,
        vp,
        setVp,
        tool,
        setTool,
        grid,
        canEdit,
        mobile,
        viewSize,
        add,
        undo,
        redo,
        canUndo: history.past.length > 0,
        canRedo: history.future.length > 0,
        announce: setSaid,
        editingText,
        setEditingText,
        cropFor,
        setCropFor,
        fit: () => fitTo(content),
      }
    : null

  useBoardKeys(ed, view === 'canvas')

  // ---------- conflict ----------

  const resolve = async (how: 'combine' | 'theirs' | 'mine') => {
    const theirs = conflict
    if (!theirs || !saved.current || !committed.current) return
    const theirsContent = contentOf(theirs)
    if (how === 'theirs') {
      saved.current = theirsContent
      committed.current = theirsContent
      base.current = theirs.version
      setContent(theirsContent)
      setHistory({ past: [], future: [] })
      setConflict(null)
      setSaveState('saved')
      return
    }
    if (how === 'combine') {
      const mine = diffContent(saved.current, committed.current)
      const merged = applyPatch(theirsContent, mine)
      saved.current = theirsContent
      base.current = theirs.version
      committed.current = merged
      setContent(merged)
      setConflict(null)
      void save()
      return
    }
    try {
      setSaveState('saving')
      const doc = await api3.putBoard(id, theirs.version, committed.current, batch.current)
      saved.current = committed.current
      base.current = doc.version
      qc.setQueryData(['board', id], doc)
      setConflict(null)
      setSaveState('saved')
    } catch (e) {
      const again = conflictBoard(e)
      if (again) setConflict(again)
      else {
        setSaveError(describeError(e))
        setSaveState('error')
      }
    }
  }

  // ---------- render ----------

  if (board.isError)
    return (
      <main className="board-screen center">
        <div>
          <p role="alert">Couldn’t open this board: {describeError(board.error)}</p>
          <button onClick={() => openScreen({ kind: 'boards' })}>All boards</button>
        </div>
      </main>
    )
  if (!ed || !content)
    return (
      <main className="board-screen center" role="status">
        Loading board…
      </main>
    )

  const rename = (name: string) => {
    const n = name.trim()
    if (n && n !== content.name) commit({ ...content, name: n })
  }
  const saveText: Record<SaveState, string> = {
    saved: 'Saved',
    unsaved: 'Unsaved changes',
    saving: 'Saving…',
    error: `Not saved: ${saveError}`,
    conflict: 'Changed elsewhere',
  }
  const trashed = board.data?.state === 'trashed'

  return (
    <main className={`board-screen${mobile ? ' mobile' : ''}`} aria-labelledby="board-h1">
      <h1 id="board-h1" className="sr-only">Board: {content.name}</h1>
      <div className="board-bar" role="toolbar" aria-label="Board tools">
        <button className="btn-icon" onClick={() => openScreen({ kind: 'boards' })} title="All boards">
          <ArrowLeft size={16} aria-hidden="true" />
          {!mobile && 'Boards'}
          {mobile && <span className="sr-only">All boards</span>}
        </button>
        {canEdit ? (
          <input
            id="board-title"
            className="board-name-input"
            aria-label="Board name"
            defaultValue={content.name}
            key={content.name}
            onBlur={(e) => rename(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
          />
        ) : (
          <span id="board-title" className="board-name" aria-hidden="true">
            {content.name}
          </span>
        )}
        <span className={`save-state ${saveState}`} role="status">
          {saveText[saveState]}
        </span>
        {saveState === 'error' && (
          <button className="small" onClick={() => void save()}>
            Retry
          </button>
        )}
        {canEdit && view === 'canvas' && (
          <>
            <div className="seg" role="group" aria-label="Tool">
              <ToolBtn on={tool === 'select'} onClick={() => setTool('select')} label="Select and move (V)" icon={<MousePointer2 size={16} />} />
              <ToolBtn on={tool === 'hand'} onClick={() => setTool('hand')} label="Pan (H, or hold Space)" icon={<Hand size={16} />} />
              <ToolBtn on={tool === 'connect'} onClick={() => setTool('connect')} label="Draw arrows: drag from one to another (C)" icon={<Spline size={16} />} />
            </div>
            <div className="seg" role="group" aria-label="Add">
              <button className="icon-btn" onClick={() => add('note')} aria-label="Add a note (N)" title="Note (N)">
                <StickyNote size={16} aria-hidden="true" />
              </button>
              <button className="icon-btn" onClick={() => add('text')} aria-label="Add text (T)" title="Text (T)">
                <Type size={16} aria-hidden="true" />
              </button>
              <button className="icon-btn" onClick={() => add('shape')} aria-label="Add a shape (R)" title="Shape (R)">
                <Square size={16} aria-hidden="true" />
              </button>
              <button className="icon-btn" onClick={() => add('color')} aria-label="Add a colour swatch" title="Colour swatch">
                <Palette size={16} aria-hidden="true" />
              </button>
              <button className="icon-btn" onClick={() => add('link')} aria-label="Add a link" title="Link">
                <Link2 size={16} aria-hidden="true" />
              </button>
              <button className={`btn-icon${libraryOpen ? ' on' : ''}`} aria-pressed={libraryOpen} onClick={() => setLibraryOpen(!libraryOpen)} title="Add items from the library (L)">
                <Images size={16} aria-hidden="true" /> Library
              </button>
            </div>
            <button
              className={`icon-btn${grid ? ' on' : ''}`}
              aria-pressed={!!grid}
              aria-label="Snap to grid"
              title="Snap to a 20 px grid"
              onClick={() => commit({ ...content, canvas: { ...content.canvas, grid: grid ? 0 : SNAP, snap: undefined } })}
            >
              <Grid3x3 size={16} aria-hidden="true" />
            </button>
          </>
        )}
        {canEdit && (
          <div className="seg" role="group" aria-label="History">
            <button className="icon-btn" onClick={undo} disabled={!history.past.length} aria-label="Undo (⌘Z)" title="Undo (⌘Z)">
              <Undo2 size={16} aria-hidden="true" />
            </button>
            <button className="icon-btn" onClick={redo} disabled={!history.future.length} aria-label="Redo (⇧⌘Z)" title="Redo (⇧⌘Z)">
              <Redo2 size={16} aria-hidden="true" />
            </button>
          </div>
        )}
        <span className="grow" />
        {view === 'canvas' && (
          <div className="seg" role="group" aria-label="Zoom">
            <button className="icon-btn" aria-label="Zoom out (−)" title="Zoom out (−)" onClick={() => zoomBy(1 / 1.25)}>
              <ZoomOut size={16} aria-hidden="true" />
            </button>
            <button className="zoom-pct" onClick={() => zoomTo(1)} title="100% (1)" aria-label={`Zoom ${Math.round(vp.zoom * 100)}%, press for 100%`}>
              {Math.round(vp.zoom * 100)}%
            </button>
            <button className="icon-btn" aria-label="Zoom in (+)" title="Zoom in (+)" onClick={() => zoomBy(1.25)}>
              <ZoomIn size={16} aria-hidden="true" />
            </button>
            <button className="icon-btn" aria-label="Fit the board (0)" title="Fit (0)" onClick={() => fitTo(content)}>
              <Maximize size={16} aria-hidden="true" />
            </button>
          </div>
        )}
        <button className={`btn-icon${view === 'list' ? ' on' : ''}`} aria-pressed={view === 'list'} onClick={() => setView(view === 'list' ? 'canvas' : 'list')} title="A list of everything on the board, for keyboards and screen readers">
          <LayoutList size={16} aria-hidden="true" /> List
        </button>
        {canEdit && <BackgroundMenu ed={ed} />}
        <ExportMenu id={id} name={content.name} />
        {caps.canEdit && (
          <button
            className="icon-btn"
            aria-label="Share a link to this board"
            title="Share a read-only link to this board"
            onClick={() => m45.openShare({ kind: 'board', id, itemIds: [], name: content.name })}
          >
            <Share2 size={16} aria-hidden="true" />
          </button>
        )}
        {mobile && (
          <PhoneBoardMenu
            id={id}
            name={content.name}
            listOn={view === 'list'}
            onToggleList={() => setView(view === 'list' ? 'canvas' : 'list')}
            onShare={caps.canEdit ? () => m45.openShare({ kind: 'board', id, itemIds: [], name: content.name }) : undefined}
          />
        )}
      </div>

      {conflict && (
        <div className="board-conflict" role="alert">
          <p>
            <strong>Changed elsewhere.</strong> This board was edited in another tab or by an agent (now version {conflict.version}) while you were
            working. Your changes aren’t saved yet.
          </p>
          <div className="dialog-actions">
            <button className="btn-primary" onClick={() => void resolve('combine')} title="Their version, with your changes put on top">
              Combine: mine on top of theirs
            </button>
            <button onClick={() => void resolve('theirs')}>Use theirs (drop mine)</button>
            <button className="danger" onClick={() => void resolve('mine')}>
              Overwrite with mine
            </button>
          </div>
        </div>
      )}
      {trashed && (
        <div className="board-conflict" role="status">
          This board is in the boards trash. Restore it from Boards → Trash to edit it.
        </div>
      )}

      <span className="sr-only" role="status" aria-live="polite">
        {said}
      </span>

      <div className="board-body">
        {canEdit && libraryOpen && view === 'canvas' && <BoardLibrary ed={ed} onClose={() => setLibraryOpen(false)} />}
        {view === 'canvas' ? <BoardCanvas ed={ed} viewportRef={viewport} /> : <BoardList ed={ed} />}
        {canEdit && (sel.length > 0 || edge) && <BoardInspector ed={ed} />}
      </div>
    </main>
  )

  function zoomBy(f: number) {
    const { w, h } = viewSize()
    const zoom = clampZoom(vp.zoom * f)
    const cx = w / 2
    const cy = h / 2
    setVp({ zoom, x: cx - ((cx - vp.x) / vp.zoom) * zoom, y: cy - ((cy - vp.y) / vp.zoom) * zoom })
  }
  function zoomTo(z: number) {
    zoomBy(z / vp.zoom)
  }
}

function ToolBtn({ on, onClick, label, icon }: { on: boolean; onClick: () => void; label: string; icon: React.ReactNode }) {
  return (
    <button className={`icon-btn${on ? ' on' : ''}`} aria-pressed={on} aria-label={label} title={label} onClick={onClick}>
      <span aria-hidden="true">{icon}</span>
    </button>
  )
}

function ExportMenu({ id, name }: { id: string; name: string }) {
  return (
    <details className="board-menu">
      <summary className="btn-icon">
        <Download size={16} aria-hidden="true" /> Export
      </summary>
      <div className="board-menu-pop" role="group" aria-label="Export">
        <a href={api3.exportUrl(id, 'png', 2)} download={`${name}.png`}>
          PNG image <span className="dim">(2×)</span>
        </a>
        <a href={api3.exportUrl(id, 'pdf', 1)} download={`${name}.pdf`}>
          PDF
        </a>
        <a href={api3.exportUrl(id, 'html')} download={`${name}.html`}>
          One HTML file <span className="dim">(images inside, opens anywhere)</span>
        </a>
        <p className="hint">Exports are made from the last saved version.</p>
      </div>
    </details>
  )
}

function BackgroundMenu({ ed }: { ed: Editor }) {
  const c = ed.content
  return (
    <details className="board-menu">
      <summary className="btn-icon" aria-label="Board background" title="Board background">
        <span className="bg-dot" style={{ background: c.canvas.bg }} aria-hidden="true" />
      </summary>
      <div className="board-menu-pop" role="group" aria-label="Board background">
        {CANVAS_BGS.map((b) => (
          <button key={b.bg} className={`btn-icon${c.canvas.bg === b.bg ? ' on' : ''}`} aria-pressed={c.canvas.bg === b.bg} onClick={() => ed.commit({ ...c, canvas: { ...c.canvas, bg: b.bg } })}>
            <span className="bg-dot" style={{ background: b.bg }} aria-hidden="true" /> {b.name}
          </button>
        ))}
      </div>
    </details>
  )
}
