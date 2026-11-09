import { useVirtualizer } from '@tanstack/react-virtual'
import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { emptyQuery, thumbUrl, type Item } from '../api'
import { fmtDuration, plural, tileLabel } from '../format'
import {
  isNavKey,
  layoutRows,
  mobileTileSize,
  nextIndex,
  pickThumbSize,
  tilePositions,
  typicalAspect,
} from '../layout'
import { useCurrentItems, useFolderNames, useSmartFolders, useStats } from '../data'
import { emptyState, everywhere, isUnfiledView, viewTitle } from '../query'
import { markOnce, onceSeen } from '../settings'
import type { KeyLike } from '../shortcuts'
import { useAppState } from '../state'
import { useShortcut } from '../useShortcuts'
import { describeError } from '../writes'
import { BulkBar } from './BulkBar'

const KIND_ICONS: Record<string, string> = {
  image: '🖼',
  video: '🎞',
  pdf: '📄',
  other: '📦',
}

/** DOM id of a tile, so focus can follow the keyboard (and the preview can
 * hand focus back to the tile it was opened from). */
export const tileDomId = (id: string) => `tile-${id}`

const Tile = memo(function Tile({
  item,
  width,
  height,
  selected,
  tabbable,
  index,
  total,
  onPointerDown,
  onDoubleClick,
  onDragStart,
  onFocus,
  draggable,
}: {
  item: Item
  width: number
  height: number
  selected: boolean
  tabbable: boolean
  index: number
  total: number
  onPointerDown: (item: Item, e: React.MouseEvent) => void
  onDoubleClick: (item: Item) => void
  onDragStart: (item: Item, e: React.DragEvent) => void
  onFocus: (item: Item) => void
  draggable: boolean
}) {
  // tiles are cover-cropped, so pick the thumb that covers the tile at the
  // screen's pixel density (thumb_512 is sized by its short side)
  const thumb = thumbUrl(item, pickThumbSize(item, width, height, window.devicePixelRatio || 1))
  return (
    <div
      id={tileDomId(item.id)}
      role="option"
      aria-selected={selected}
      aria-setsize={total}
      aria-posinset={index + 1}
      aria-label={tileLabel(item)}
      tabIndex={tabbable ? 0 : -1}
      className={`tile ${selected ? 'selected' : ''}`}
      style={{ width, height }}
      onMouseDown={(e) => onPointerDown(item, e)}
      onDoubleClick={() => onDoubleClick(item)}
      onFocus={() => onFocus(item)}
      draggable={draggable}
      onDragStart={(e) => onDragStart(item, e)}
      title={`${item.filename} — double-click to open`}
    >
      {thumb ? (
        <img src={thumb} loading="lazy" draggable={false} alt="" />
      ) : (
        <div className={`tile-icon preview-${item.preview_status}`} aria-hidden="true">
          <span>
            {item.preview_status === 'failed'
              ? '⚠'
              : item.preview_status === 'pending'
                ? '⏳'
                : (KIND_ICONS[item.kind] ?? '📦')}
          </span>
          <span className="ext">
            {item.filename}
            {item.preview_status === 'failed' && ' · preview failed'}
          </span>
        </div>
      )}
      {item.kind === 'video' && item.duration_s != null && (
        <span className="badge" aria-hidden="true">
          {fmtDuration(item.duration_s)}
        </span>
      )}
      {item.rating > 0 && (
        <span className="badge rating" aria-hidden="true">
          {'★'.repeat(item.rating)}
        </span>
      )}
      <div className="tile-caption" aria-hidden="true">
        {item.filename}
      </div>
    </div>
  )
})

export function Grid({ mobile = false }: { mobile?: boolean }) {
  const {
    query,
    setQuery,
    smartId,
    selection,
    selectOnly,
    toggleSelect,
    rangeSelect,
    clearSelection,
    selectAll,
    setPreviewId,
    gridLayout,
    tileSize: desktopTileSize,
    mobileCols,
  } = useAppState()
  const { result, items } = useCurrentItems()
  const { data: stats } = useStats()
  const { data: smartFolders } = useSmartFolders()
  const folderNames = useFolderNames()
  const flatIds = useMemo(() => items.map((i) => i.id), [items])
  const hintId = useId()

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const [width, setWidth] = useState(800)

  // callback ref: the scroll container only mounts after the first page
  // loads, so a mount-time effect would never see it
  const attachScroll = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    observer.current = null
    scrollRef.current = el
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    observer.current = ro
    setWidth(el.clientWidth)
  }, [])

  // .grid-scroll has 8 px side padding
  const aspect = useMemo(
    () => (mobile && gridLayout === 'justified' ? typicalAspect(items) : 1),
    [mobile, gridLayout, items],
  )
  const tileSize = mobile ? mobileTileSize(width - 16, mobileCols, aspect) : desktopTileSize
  const rows = useMemo(
    () => layoutRows(items, width - 16, tileSize, gridLayout),
    [items, width, tileSize, gridLayout],
  )
  const rowCount = rows.length

  // item index -> its row and horizontal centre, for up/down arrow keys
  const positions = useMemo(() => tilePositions(rows), [rows])

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.height ?? tileSize) + 8,
    overscan: 4,
    // the bulk bar and toasts float over the bottom: keep the current tile clear
    scrollPaddingEnd: 80,
  })

  // re-measure when the row heights change (resize, slider, layout switch)
  useEffect(() => {
    virtualizer.measure()
  }, [rows]) // eslint-disable-line react-hooks/exhaustive-deps

  const virtualRows = virtualizer.getVirtualItems()

  useEffect(() => {
    const last = virtualRows.at(-1)
    if (!last) return
    if (last.index >= rowCount - 2 && result.hasNextPage && !result.isFetchingNextPage) {
      result.fetchNextPage()
    }
  }, [virtualRows, rowCount, result.hasNextPage, result.isFetchingNextPage]) // eslint-disable-line react-hooks/exhaustive-deps

  // the keyboard cursor (roving tabindex): one tile is in the Tab order
  const [focusedId, setFocusedId] = useState<string | null>(null)
  const wantFocus = useRef(false)

  // a tile scrolled out of the rendered rows: focus it once the virtualizer
  // has rendered it (timers, not animation frames, which a hidden tab skips)
  useEffect(() => {
    if (!wantFocus.current || !focusedId) return
    let tries = 0
    let timer: ReturnType<typeof setTimeout>
    const tick = () => {
      const el = document.getElementById(tileDomId(focusedId))
      if (el) {
        wantFocus.current = false
        el.focus({ preventScroll: true })
      } else if (tries++ < 30) timer = setTimeout(tick, 16)
    }
    tick()
    return () => clearTimeout(timer)
  }, [focusedId])

  const moveCursor = useCallback(
    (index: number) => {
      const id = flatIds[index]
      if (!id) return
      setFocusedId(id)
      if (positions[index]) virtualizer.scrollToIndex(positions[index].row)
      const el = document.getElementById(tileDomId(id))
      if (el) el.focus({ preventScroll: true })
      else wantFocus.current = true
    },
    [flatIds, positions, virtualizer],
  )

  // the focused tile left the list (trashed with Delete, filed out of
  // Unfiled): the cursor moves to the tile that took its place, so focus
  // isn't dropped on the page
  const lastIndex = useRef(-1)
  if (focusedId) {
    const i = flatIds.indexOf(focusedId)
    if (i >= 0) lastIndex.current = i
  }
  useEffect(() => {
    if (!focusedId || flatIds.includes(focusedId) || !flatIds.length) return
    const lostFocus = !document.activeElement || document.activeElement === document.body
    const next = flatIds[Math.min(lastIndex.current, flatIds.length - 1)]
    if (!next) return
    setFocusedId(next)
    if (lostFocus) wantFocus.current = true
  }, [flatIds, focusedId])

  const handlePointerDown = useCallback(
    (item: Item, e: React.MouseEvent) => {
      if (e.button !== 0) return
      setFocusedId(item.id)
      if (e.shiftKey) rangeSelect(item.id, flatIds)
      else if (e.metaKey || e.ctrlKey) toggleSelect(item.id)
      else if (!selection.has(item.id)) selectOnly(item.id)
    },
    [flatIds, rangeSelect, toggleSelect, selectOnly, selection],
  )

  const handleDragStart = useCallback(
    (item: Item, e: React.DragEvent) => {
      const ids = selection.has(item.id) ? [...selection] : [item.id]
      e.dataTransfer.setData('application/x-kist-items', JSON.stringify(ids))
      e.dataTransfer.effectAllowed = 'move'
    },
    [selection],
  )

  const openPreview = useCallback((item: Item) => setPreviewId(item.id), [setPreviewId])
  const onTileFocus = useCallback((item: Item) => setFocusedId(item.id), [])

  /**
   * The grid's keys (a multi-select listbox): arrows move and select,
   * Shift+arrows extend, Ctrl/⌘+arrows move without selecting, Space
   * toggles the current tile, Enter opens it full screen, Home/End jump,
   * ⌘A selects all, Escape clears. Delete is BulkBar's.
   */
  const onGridKey = (e: KeyLike): boolean => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault()
      selectAll(flatIds)
      return true
    }
    if (e.key === 'Escape') {
      if (selection.size === 0) return false
      e.preventDefault()
      clearSelection()
      return true
    }
    const cur = focusedId ? flatIds.indexOf(focusedId) : -1
    if (e.key === 'Enter') {
      const id = cur >= 0 ? focusedId : selection.size === 1 ? [...selection][0] : null
      if (!id) return false
      e.preventDefault()
      setPreviewId(id)
      return true
    }
    if (e.key === ' ') {
      e.preventDefault()
      if (cur >= 0) toggleSelect(flatIds[cur])
      else moveCursor(0)
      return true
    }
    let next = -1
    if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = flatIds.length - 1
    else if (isNavKey(e.key)) next = nextIndex(e.key, cur, flatIds.length, rows, positions)
    else return false
    e.preventDefault()
    if (next < 0) return true
    moveCursor(next)
    if (e.metaKey || e.ctrlKey) return true // move only
    if (e.shiftKey) rangeSelect(flatIds[next], flatIds)
    else selectOnly(flatIds[next])
    return true
  }

  // with nothing focused (just loaded, or after a click on empty space) the
  // grid keys still work, and put focus on the tile they move to
  useShortcut('grid', (e) => {
    const t = e.target as HTMLElement | null
    if (t && t !== document.body && t !== document.documentElement) return false
    return onGridKey(e)
  })

  // first visit with an empty Unfiled: show the library instead (the URL /
  // stays Unfiled for everyone who has been here before)
  const total = result.data?.pages[0]?.total ?? items.length
  useEffect(() => {
    if (!result.isSuccess || onceSeen('visited')) return
    markOnce('visited')
    if (isUnfiledView(query) && total === 0 && !query.q && window.location.pathname === '/')
      setQuery({ ...emptyQuery, order: query.order })
  }, [result.isSuccess]) // eslint-disable-line react-hooks/exhaustive-deps

  const smartName = smartId ? (smartFolders ?? []).find((s) => s.id === smartId)?.name : null
  const title = viewTitle(query, { folder: (id) => folderNames.get(id), smart: smartName })

  if (result.isLoading)
    return (
      <main className="grid-area center" role="status">
        Loading library…
      </main>
    )
  if (result.isError)
    return (
      <main className="grid-area center">
        <div role="alert" className="error">
          Failed to load: {describeError(result.error)}
        </div>
      </main>
    )

  const empty =
    items.length === 0
      ? emptyState(query, {
          libraryEmpty: stats?.total_items === 0,
          mobile,
          title,
          smart: !!smartId,
        })
      : null

  // the cursor tile, or (when it isn't rendered) the first rendered tile,
  // is the grid's one Tab stop
  const rendered = virtualRows.flatMap((r) => rows[r.index]?.tiles.map((t) => items[t.index]?.id) ?? [])
  const tabStop = focusedId && rendered.includes(focusedId) ? focusedId : rendered[0]

  return (
    <main className={`grid-area${mobile && selection.size === 1 ? ' sheet-open' : ''}`}>
      <div className="grid-meta">
        <h2 className="grid-title">{title}</h2>
        <span role="status">
          {plural(total, 'item')}
          {selection.size > 1 ? `, ${selection.size} selected` : ''}
        </span>
        <span aria-hidden="true">{result.isFetching ? ' · refreshing…' : ''}</span>
        {isUnfiledView(query) && (
          <p className="view-subtitle">
            Items with no folder and no tag. Tag or file them to clear this list; they stay in All items.
          </p>
        )}
      </div>
      <span id={hintId} className="sr-only">
        Arrow keys move and select, Space adds or removes one item, Shift with arrows extends the selection,
        Control with arrows moves without selecting, Enter opens full screen
        {mobile ? '' : ', Delete moves to Trash'}.
      </span>
      <div
        className="grid-scroll"
        ref={attachScroll}
        role={items.length ? 'listbox' : undefined}
        aria-multiselectable={items.length ? 'true' : undefined}
        aria-label={items.length ? `${title}, ${plural(total, 'item')}` : undefined}
        aria-describedby={items.length ? hintId : undefined}
        onKeyDown={(e) => {
          if (!(e.target as HTMLElement).closest('[role=option]')) return
          if (onGridKey(e)) e.stopPropagation()
        }}
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) clearSelection()
        }}
      >
        <div role="presentation" style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualRows.map((row) => (
            <div
              key={row.key}
              role="presentation"
              className="grid-row"
              style={{ transform: `translateY(${row.start}px)` }}
            >
              {rows[row.index]?.tiles.map((t) => {
                const item = items[t.index]
                return (
                  <Tile
                    key={item.id}
                    item={item}
                    index={t.index}
                    total={total}
                    width={t.width}
                    height={rows[row.index].height}
                    selected={selection.has(item.id)}
                    tabbable={item.id === tabStop}
                    onPointerDown={handlePointerDown}
                    onDoubleClick={openPreview}
                    onDragStart={handleDragStart}
                    onFocus={onTileFocus}
                    draggable={!mobile}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      {empty && (
        <div className="grid-empty">
          <p>{empty.text}</p>
          {empty.action === 'everywhere' && (
            <button onClick={() => setQuery(everywhere(query))}>Search everything</button>
          )}
          {empty.action === 'all' && (
            <button onClick={() => setQuery({ ...emptyQuery, order: query.order })}>Show all items</button>
          )}
        </div>
      )}
      {!mobile && <BulkBar />}
    </main>
  )
}
