import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { thumbUrl, type Item } from '../api'
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
  size,
  selected,
  onPointerDown,
  onDoubleClick,
  onDragStart,
}: {
  item: Item
  size: number
  selected: boolean
  onPointerDown: (e: React.MouseEvent) => void
  onDoubleClick: () => void
  onDragStart: (e: React.DragEvent) => void
}) {
  // tiles are square + cover-cropped, so the thumb's short side must reach
  // the tile's physical pixel size; thumb_512 is sized by its short side
  const physical = (size - 8) * (window.devicePixelRatio || 1)
  const thumb = thumbUrl(item, physical <= 512 ? 512 : 1024)
  return (
    <div
      className={`tile ${selected ? 'selected' : ''}`}
      style={{ width: size - 8, height: size - 8 }}
      onMouseDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      draggable
      onDragStart={onDragStart}
      title={item.filename}
    >
      {thumb ? (
        <img src={thumb} loading="lazy" draggable={false} alt={item.filename} />
      ) : (
        <div className="tile-icon">
          <span>{KIND_ICONS[item.kind] ?? '📦'}</span>
          <span className="ext">.{item.ext}</span>
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

export function Grid() {
  const { query, selection, selectOnly, toggleSelect, rangeSelect, clearSelection, selectAll, previewId, setPreviewId } =
    useAppState()
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

  const cols = Math.max(2, Math.floor((width - 16) / 210))
  const cellSize = Math.floor((width - 16) / cols)
  const rowCount = Math.ceil(items.length / cols)

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => cellSize,
    overscan: 4,
  })

  // re-measure when the column size changes
  useEffect(() => {
    virtualizer.measure()
  }, [cellSize]) // eslint-disable-line react-hooks/exhaustive-deps

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
      else if (e.key === 'ArrowDown') next = Math.min(idx + cols, flatIds.length - 1)
      else if (e.key === 'ArrowUp') next = Math.max(idx - cols, 0)
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
        virtualizer.scrollToIndex(Math.floor(next / cols))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [flatIds, cols, previewId, rangeSelect, selectOnly, selectAll, clearSelection, setPreviewId, virtualizer])

  if (result.isLoading) return <main className="grid-area center">Loading library…</main>
  if (result.isError)
    return <main className="grid-area center error">Failed to load: {String(result.error)}</main>

  const total = result.data?.pages[0]?.total ?? items.length

  return (
    <main className="grid-area">
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
              {Array.from({ length: cols }, (_, c) => {
                const item = items[row.index * cols + c]
                if (!item) return <div key={c} style={{ width: cellSize - 8 }} />
                return (
                  <Tile
                    key={item.id}
                    item={item}
                    size={cellSize}
                    selected={selection.has(item.id)}
                    onPointerDown={(e) => handlePointerDown(item, e)}
                    onDoubleClick={() => setPreviewId(item.id)}
                    onDragStart={(e) => handleDragStart(item, e)}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      <BulkBar items={itemById} />
    </main>
  )
}
