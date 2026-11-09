import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { thumbUrl, type Item } from '../api'
import { layoutRows, mobileTileSize, pickThumbSize, typicalAspect } from '../layout'
import { flattenItems, useAppState, useItems } from '../state'
import { BulkBar } from './BulkBar'

const KIND_ICONS: Record<string, string> = {
  image: '🖼',
  video: '🎞',
  pdf: '📄',
  other: '📦',
}

function fmtDuration(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

function Tile({
  item,
  width,
  height,
  selected,
  onPointerDown,
  onDoubleClick,
  onDragStart,
  draggable,
}: {
  item: Item
  width: number
  height: number
  selected: boolean
  onPointerDown: (e: React.MouseEvent) => void
  onDoubleClick: () => void
  onDragStart: (e: React.DragEvent) => void
  draggable: boolean
}) {
  // tiles are cover-cropped, so pick the thumb that covers the tile at the
  // screen's pixel density (thumb_512 is sized by its short side)
  const thumb = thumbUrl(item, pickThumbSize(item, width, height, window.devicePixelRatio || 1))
  return (
    <div
      className={`tile ${selected ? 'selected' : ''}`}
      style={{ width, height }}
      onMouseDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      draggable={draggable}
      onDragStart={onDragStart}
      title={item.filename}
    >
      {thumb ? (
        <img src={thumb} loading="lazy" draggable={false} alt={item.filename} />
      ) : (
        <div className={`tile-icon preview-${item.preview_status}`}>
          <span>
            {item.preview_status === 'failed'
              ? '⚠'
              : item.preview_status === 'pending'
                ? '⏳'
                : (KIND_ICONS[item.kind] ?? '📦')}
          </span>
          <span className="ext">
            .{item.ext}
            {item.preview_status === 'failed' && ' · preview failed'}
          </span>
        </div>
      )}
      {item.kind === 'video' && item.duration_s != null && (
        <span className="badge">{fmtDuration(item.duration_s)}</span>
      )}
      {item.rating > 0 && <span className="badge rating">{'★'.repeat(item.rating)}</span>}
      <div className="tile-caption">{item.filename}</div>
    </div>
  )
}

export function Grid({ mobile = false }: { mobile?: boolean }) {
  const {
    query,
    selection,
    selectOnly,
    toggleSelect,
    rangeSelect,
    clearSelection,
    selectAll,
    previewId,
    setPreviewId,
    gridLayout,
    tileSize: desktopTileSize,
    mobileCols,
  } = useAppState()
  const result = useItems(query)
  const items = useMemo(() => flattenItems(result.data?.pages), [result.data])
  const flatIds = useMemo(() => items.map((i) => i.id), [items])
  const itemById = useMemo(() => new Map(items.map((i) => [i.id, i])), [items])

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
  const positions = useMemo(() => {
    const pos: { row: number; center: number }[] = []
    rows.forEach((row, r) => {
      let x = 0
      for (const t of row.tiles) {
        pos[t.index] = { row: r, center: x + 4 + t.width / 2 }
        x += t.width + 8
      }
    })
    return pos
  }, [rows])

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.height ?? tileSize) + 8,
    overscan: 4,
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

  const focusedId = useRef<string | null>(null)

  const handlePointerDown = useCallback(
    (item: Item, e: React.MouseEvent) => {
      if (e.button !== 0) return
      focusedId.current = item.id
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

  // keyboard: arrows navigate, enter previews, delete trashes (handled in BulkBar), cmd-a selects all
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target.closest('input, textarea, select, [contenteditable]')) return
      if (previewId) return // Preview handles its own keys

      if ((e.metaKey || e.ctrlKey) && e.key === 'a') {
        e.preventDefault()
        selectAll(flatIds)
        return
      }
      if (e.key === 'Escape') {
        clearSelection()
        return
      }
      const idx = focusedId.current ? flatIds.indexOf(focusedId.current) : -1
      let next = -1
      if (e.key === 'ArrowRight') next = Math.min(idx + 1, flatIds.length - 1)
      else if (e.key === 'ArrowLeft') next = Math.max(idx - 1, 0)
      else if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && positions[idx]) {
        // the tile in the next/previous row whose centre is nearest
        const here = positions[idx]
        const target = rows[here.row + (e.key === 'ArrowDown' ? 1 : -1)]
        if (!target) next = idx
        else {
          next = target.tiles[0].index
          for (const t of target.tiles) {
            if (Math.abs(positions[t.index].center - here.center) < Math.abs(positions[next].center - here.center))
              next = t.index
          }
        }
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') next = 0
      else if (e.key === 'Enter' && focusedId.current) {
        setPreviewId(focusedId.current)
        return
      } else return

      e.preventDefault()
      if (next < 0 && flatIds.length) next = 0
      if (next >= 0 && flatIds[next]) {
        focusedId.current = flatIds[next]
        if (e.shiftKey) rangeSelect(flatIds[next], flatIds)
        else selectOnly(flatIds[next])
        if (positions[next]) virtualizer.scrollToIndex(positions[next].row)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [flatIds, rows, positions, previewId, rangeSelect, selectOnly, selectAll, clearSelection, setPreviewId, virtualizer])

  if (result.isLoading) return <main className="grid-area center">Loading library…</main>
  if (result.isError)
    return <main className="grid-area center error">Failed to load: {String(result.error)}</main>

  const total = result.data?.pages[0]?.total ?? items.length

  return (
    <main className={`grid-area${mobile && selection.size === 1 ? ' sheet-open' : ''}`}>
      <div className="grid-meta">
        {query.deleted ? 'Trash · ' : ''}
        {total} item{total === 1 ? '' : 's'}
        {result.isFetching ? ' · refreshing…' : ''}
      </div>
      <div
        className="grid-scroll"
        ref={attachScroll}
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) clearSelection()
        }}
      >
        {items.length === 0 && (
          <div className="center empty">
            {query.deleted ? 'Trash is empty.' : 'No items. Drop files anywhere to upload.'}
          </div>
        )}
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualRows.map((row) => (
            <div
              key={row.key}
              className="grid-row"
              style={{ transform: `translateY(${row.start}px)` }}
            >
              {rows[row.index]?.tiles.map((t) => {
                const item = items[t.index]
                return (
                  <Tile
                    key={item.id}
                    item={item}
                    width={t.width}
                    height={rows[row.index].height}
                    selected={selection.has(item.id)}
                    onPointerDown={(e) => handlePointerDown(item, e)}
                    onDoubleClick={() => setPreviewId(item.id)}
                    onDragStart={(e) => handleDragStart(item, e)}
                    draggable={!mobile}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      {!mobile && <BulkBar items={itemById} />}
    </main>
  )
}
