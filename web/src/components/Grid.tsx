import { cutoutPreviewUrl, isCutout } from '../cutouts' // F7
import { defaultRangeExtractor, useVirtualizer, type Range } from '@tanstack/react-virtual'
import { AiBadge } from './credits/Credits' // F4
import { creditsTileLabel } from '../credits'
import { Check, File, FileText, Film, Hourglass, Image as ImageIcon, Lock, Star, TriangleAlert, type LucideIcon } from 'lucide-react'
import { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { emptyQuery, thumbUrl, type Item } from '../api'
import { fmtDuration, tileLabel } from '../format'
import {
  feedRows,
  isNavKey,
  layoutRows,
  mobileTileSize,
  nextIndex,
  pickThumbSize,
  revealScrollTop,
  rowReach,
  rowStarts,
  tilePositions,
  typicalAspect,
} from '../layout'
import { useCaps, useCurrentItems, useFolderNames, useFolders, useItem, useSmartFolders, useStats } from '../data'
import { emptyState, everywhere, gridCountText, isUnfiledView, ratingsShown, sidebarCount, viewTitle } from '../query'
import { placeholderFor, placeholderStyle } from '../placeholder'
import { markOnce, onceSeen } from '../settings'
import { densityGap, densityTileSize } from '../theme'
import type { KeyLike } from '../shortcuts'
import { useAppState } from '../state'
import { useShortcut } from '../useShortcuts'
import { describeError } from '../writes'
import { BulkBar } from './BulkBar'
import { EmptyState, ItemContextMenu, RankedError, rankedErrorText, SearchNote, usePersonName, showMatchBadge, StackBadge, TileMatch, tileExtra, useExpandedStacks, useStackToggle } from './GridExtras'
import { searchTitle } from '../search'
import { KindBar } from './kinds/KindsUI'
import { CullBadges, cullText } from './m3/CullControls'
import { GuestTileBadge } from './proofing/TileBadge'
import { FirstRun } from './m3/FirstRun'
import { ScrubHover } from './m45/ScrubHover'
import { Scrubber } from './Scrubber'
import { ViewControls } from './TopBar'
import { usePinchColumns, useWheelSize } from '../useGridZoom'
import { useScrubWindow, type Anchor } from '../useScrubWindow'
import { anchoredScrollTop } from '../zoom'
import '../m6grid.css'

const KIND_ICONS: Record<string, LucideIcon> = {
  image: ImageIcon,
  video: Film,
  pdf: FileText,
  other: File,
}

/** The thumbnail fades in over the placeholder once it has loaded (an
 * already-cached one shows at once). One that fails to load stays hidden,
 * so the placeholder shows instead of a broken-image icon. */
function markLoaded(img: HTMLImageElement | null) {
  if (img && img.complete && img.naturalWidth > 0) img.classList.add('loaded')
}
const onImgLoad = (e: React.SyntheticEvent<HTMLImageElement>) => e.currentTarget.classList.add('loaded')
/** A thumbnail that won't load stops the loading sheen (A7) and lets the
 * badges show over the placeholder. */
const onImgError = (e: React.SyntheticEvent<HTMLImageElement>) => e.currentTarget.classList.add('failed')

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
  extraLabel = '',
  member = false,
  stacked = false,
  matchBadge = false,
  onStackClick,
  onContextMenu,
  left,
  top,
}: {
  /** Columns layout: where the tile sits in its row (see layout.ts) */
  left?: number
  top?: number
  extraLabel?: string
  member?: boolean
  /** a closed stack's entry: drawn with a second sheet behind it (B16) */
  stacked?: boolean
  matchBadge?: boolean
  onStackClick?: (item: Item) => void
  onContextMenu?: (item: Item, e: React.MouseEvent) => void
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
  const cutout = isCutout(item) && item.preview_status === 'ready' // F7: shown transparent, on a checkerboard
  const thumb = cutout ? cutoutPreviewUrl(item, 1024) : thumbUrl(item, pickThumbSize(item, width, height, window.devicePixelRatio || 1))
  const placeholder = useMemo(() => (thumb ? placeholderStyle(placeholderFor(item)) : {}), [thumb, item])
  const KindIcon =
    item.preview_status === 'failed'
      ? TriangleAlert
      : item.preview_status === 'pending'
        ? Hourglass
        : (KIND_ICONS[item.kind] ?? File)
  return (
    <div
      id={tileDomId(item.id)}
      role="option"
      aria-selected={selected}
      aria-setsize={total}
      aria-posinset={index + 1}
      aria-label={tileLabel(item) + (cullText(item) ? `, ${cullText(item)}` : '') + creditsTileLabel(item.provenance) + extraLabel}
      tabIndex={tabbable ? 0 : -1}
      className={`tile${cutout ? ' cutout-tile' : ''}${selected ? ' selected' : ''}${thumb ? '' : ' no-thumb'}${member ? ' stack-member' : ''}${stacked ? ' stacked' : ''}${item.rating > 0 ? ' rated' : ''}`}
      style={
        left === undefined
          ? { width, height, ...placeholder }
          : { width, height, position: 'absolute', left, top, ...placeholder }
      }
      onMouseDown={(e) => {
        if (onStackClick && (e.target as HTMLElement).closest('[data-stack-toggle]')) {
          e.preventDefault()
          onStackClick(item)
          return
        }
        onPointerDown(item, e)
      }}
      onContextMenu={onContextMenu ? (e) => onContextMenu(item, e) : undefined}
      onDoubleClick={() => onDoubleClick(item)}
      onFocus={() => onFocus(item)}
      draggable={draggable}
      onDragStart={(e) => onDragStart(item, e)}
      title={`${item.filename} — double-click to open`}
    >
      {thumb ? (
        <img
          src={thumb}
          loading="lazy"
          decoding="async"
          draggable={false}
          alt=""
          ref={markLoaded}
          onLoad={onImgLoad}
          onError={onImgError}
        />
      ) : (
        <div className={`tile-icon preview-${item.preview_status}`} aria-hidden="true">
          <KindIcon size={Math.max(20, Math.min(40, Math.round(height / 5)))} />
          <span className="ext">
            {item.filename}
            {item.preview_status === 'failed' && ' · preview failed'}
          </span>
        </div>
      )}
      {thumb && item.media?.scrub ? <ScrubHover item={item} width={width} height={height} /> : null}
      {item.kind === 'video' && item.duration_s != null && (
        <span className="badge tile-dur" aria-hidden="true">
          {fmtDuration(item.duration_s)}
        </span>
      )}
      {item.rating > 0 && (
        // quiet tiles (B1): one star and the number, shown on hover, focus
        // and selection (and always while filtering or sorting by rating,
        // .grid-scroll.ratings-shown); never on phone tiles (CSS). The
        // rating is in the tile's aria-label either way.
        <span className="badge rating" aria-hidden="true">
          <Star size={14} fill="currentColor" strokeWidth={0} />
          {item.rating}
        </span>
      )}
      <CullBadges item={item} />
      <GuestTileBadge itemId={item.id} />
      {item.protected && (
        <span className="badge lock" aria-hidden="true" title="Protected">
          <Lock size={14} />
        </span>
      )}
      <TileMatch item={item} badge={matchBadge} />
      <AiBadge item={item} />
      <StackBadge item={item} member={member} />
      {selected && (
        <>
          <span className="sel-corners" aria-hidden="true" />
          <span className="sel-check" aria-hidden="true">
            <Check size={14} />
          </span>
        </>
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
    tileSize: sliderSize,
    setTileSize,
    mobileCols,
    setMobileCols,
    density,
  } = useAppState()
  const gap = densityGap(density)
  const desktopTileSize = densityTileSize(sliderSize, density)
  const { result, items } = useCurrentItems()
  const { data: stats } = useStats()
  const { data: smartFolders } = useSmartFolders()
  const folderNames = useFolderNames()
  const { data: folders } = useFolders()
  const caps = useCaps()
  const flatIds = useMemo(() => items.map((i) => i.id), [items])
  const hintId = useId()
  // stacks opened in place: their members (not the entry) get a marker
  const expanded = useExpandedStacks()
  const toggleStack = useStackToggle()
  const memberIds = useMemo(() => {
    const seen = new Set<string>()
    const out = new Set<string>()
    for (const it of items) {
      if (!it.stack_id || !expanded.has(it.stack_id)) continue
      if (seen.has(it.stack_id)) out.add(it.id)
      else seen.add(it.stack_id)
    }
    return out
  }, [items, expanded])
  const [ctxMenu, setCtxMenu] = useState<{ item: Item; x: number; y: number } | null>(null)
  const onTileContextMenu = useCallback(
    (item: Item, e: React.MouseEvent) => {
      e.preventDefault()
      if (!selection.has(item.id)) selectOnly(item.id)
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
      // the keyboard's context-menu key reports 0,0: open by the tile
      const kb = e.clientX === 0 && e.clientY === 0
      setCtxMenu({ item, x: kb ? r.left + 12 : e.clientX, y: kb ? r.top + 12 : e.clientY })
    },
    [selection, selectOnly],
  )
  const closeCtx = useCallback(() => setCtxMenu(null), [])

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const [width, setWidth] = useState(800)
  // the same element as state, for the listeners that need it attached
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null)

  // callback ref: the scroll container only mounts after the first page
  // loads, so a mount-time effect would never see it
  const attachScroll = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    observer.current = null
    scrollRef.current = el
    setScrollEl(el)
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
  const tileSize = mobile ? mobileTileSize(width - 16, mobileCols, aspect, gap) : desktopTileSize
  // phone, 1 column: the feed (every item full width, in its own shape)
  const feed = mobile && mobileCols === 1
  const rows = useMemo(
    () => (feed ? feedRows(items, width - 16, gap) : layoutRows(items, width - 16, tileSize, gridLayout, gap)),
    [feed, items, width, tileSize, gridLayout, gap],
  )
  const rowCount = rows.length

  // item index -> its row and horizontal centre, for up/down arrow keys
  const positions = useMemo(() => tilePositions(rows, gap), [rows, gap])
  const loadedStarts = useMemo(() => rowStarts(rows, gap), [rows, gap])
  // Columns tiles overhang their row: render from the first row reaching in
  const reach = useMemo(() => rowReach(rows, gap), [rows, gap])
  const rangeExtractor = useCallback(
    (range: Range) =>
      defaultRangeExtractor({ ...range, startIndex: Math.min(range.startIndex, reach[range.startIndex] ?? range.startIndex) }),
    [reach],
  )

  // what to restore after the next relayout: the item under the fingers or
  // pointer (zoom), the rows on screen (a month loaded above), or the top of
  // a window that just jumped (date scrubber)
  const anchorRef = useRef<Anchor | null>(null)
  const setAnchor = useCallback((a: Anchor) => {
    anchorRef.current = a
  }, [])

  // the date scrubber's window over the list (date sorts only)
  const scrub = useScrubWindow({
    query,
    result,
    items,
    rows,
    rowStartsLoaded: loadedStarts,
    gap,
    avail: width - 16,
    tileSize,
    layoutKey: `${width}|${tileSize}|${gridLayout}|${gap}|${feed}`,
    scrollEl: () => scrollRef.current,
    setAnchor,
  })
  const scrubbing = !!scrub.geom

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.height ?? tileSize) + gap,
    overscan: 4,
    // the bulk bar and toasts float over the bottom: keep the current tile clear
    scrollPaddingEnd: 80,
    // the scrubber's estimate of the months not loaded above and below
    paddingStart: scrub.padStart,
    paddingEnd: scrub.padEnd,
    rangeExtractor,
  })

  // re-measure when the row heights change (resize, slider, layout switch)
  useEffect(() => {
    virtualizer.measure()
  }, [rows, scrub.padStart]) // eslint-disable-line react-hooks/exhaustive-deps

  // keep the view steady across relayouts (see anchorRef)
  const prevPad = useRef(0)
  const prevQuery = useRef(query)
  useLayoutEffect(() => {
    const el = scrollRef.current
    const a = anchorRef.current
    const pad = scrub.padStart
    if (el && a) {
      // the virtualizer re-measures in an effect, after this: size the
      // content now so the scroll offset below isn't clamped to the old height
      virtualizer.measure()
      if (contentRef.current) contentRef.current.style.height = `${virtualizer.getTotalSize()}px`
    }
    const sameList = prevQuery.current === query
    prevQuery.current = query
    if (!sameList) anchorRef.current = null
    else if (el && a) {
      anchorRef.current = null
      if (a.kind === 'pos') el.scrollTop = scrub.offsetOf(a.pos)
      else {
        const i = items.findIndex((it) => it.id === a.id)
        const p = positions[i]
        if (p && rows[p.row])
          el.scrollTop = anchoredScrollTop(
            pad + loadedStarts[p.row] + (p.top ?? 0),
            p.height ?? rows[p.row].height,
            a.frac,
            a.pointY,
          )
      }
    } else if (el && sameList && pad !== prevPad.current && el.scrollTop >= prevPad.current && prevPad.current > 0) {
      // the estimate above the loaded rows changed: the rows stay put
      el.scrollTop += pad - prevPad.current
    }
    prevPad.current = pad
  }, [rows, scrub.padStart, query]) // eslint-disable-line react-hooks/exhaustive-deps

  /** Remember the item at a screen point, to keep it there after a zoom. */
  const captureAt = useCallback(
    (x: number, y: number) => {
      const el = scrollRef.current
      if (!el) return
      const box = el.getBoundingClientRect()
      const hit = document.elementFromPoint(x, y)?.closest<HTMLElement>('[role=option][id^="tile-"]')
      let tile = hit && el.contains(hit) ? hit : null
      if (!tile) {
        // between tiles: the first tile in the row nearest the point
        let best: HTMLElement | null = null
        let bestD = Infinity
        for (const t of el.querySelectorAll<HTMLElement>('[role=option][id^="tile-"]')) {
          const r = t.getBoundingClientRect()
          const d = Math.abs((r.top + r.bottom) / 2 - y) + Math.abs((r.left + r.right) / 2 - x) / 4
          if (d < bestD) {
            bestD = d
            best = t
          }
        }
        tile = best
      }
      if (!tile) return
      const r = tile.getBoundingClientRect()
      setAnchor({
        kind: 'item',
        id: tile.id.slice('tile-'.length),
        frac: r.height > 0 ? (y - r.top) / r.height : 0,
        pointY: y - box.top,
      })
    },
    [setAnchor],
  )

  // W1.2 pinch on the phone, W1.3 ⌘/Ctrl+wheel and trackpad pinch on desktop
  usePinchColumns(scrollEl, () => contentRef.current, mobile, mobileCols, setMobileCols, captureAt)
  useWheelSize(scrollEl, !mobile, sliderSize, setTileSize, captureAt)

  const virtualRows = virtualizer.getVirtualItems()

  useEffect(() => {
    // with the scrubber, its window decides what loads (useScrubWindow)
    if (scrubbing) return
    const last = virtualRows.at(-1)
    if (!last) return
    if (last.index >= rowCount - 2 && result.hasNextPage && !result.isFetchingNextPage) {
      result.fetchNextPage()
    }
  }, [virtualRows, rowCount, result.hasNextPage, result.isFetchingNextPage, scrubbing]) // eslint-disable-line react-hooks/exhaustive-deps

  // scrubber window: load for wherever the viewport is, a moment after
  // scrolling settles (a fling or a drag through years loads only the end)
  useEffect(() => {
    if (!scrollEl || !scrubbing) return
    let t: ReturnType<typeof setTimeout>
    const onScroll = () => {
      clearTimeout(t)
      t = setTimeout(scrub.check, 140)
    }
    scrollEl.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      clearTimeout(t)
      scrollEl.removeEventListener('scroll', onScroll)
    }
  }, [scrollEl, scrubbing, scrub.check])

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
      const p = positions[index]
      const el0 = scrollRef.current
      if (p && p.top !== undefined && el0) {
        // a Columns tile can be taller than its row: reveal the tile itself
        const top = scrub.padStart + loadedStarts[p.row] + p.top
        el0.scrollTop = revealScrollTop(el0.scrollTop, el0.clientHeight, top, top + (p.height ?? 0) + gap)
      } else if (p) virtualizer.scrollToIndex(p.row)
      const el = document.getElementById(tileDomId(id))
      if (el) el.focus({ preventScroll: true })
      else wantFocus.current = true
    },
    [flatIds, positions, virtualizer, loadedStarts, scrub.padStart, gap],
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
    if ((e.key === 's' || e.key === 'S') && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const it = items[cur >= 0 ? cur : flatIds.indexOf([...selection][0] ?? '')]
      if (!it?.stack_id) return false
      e.preventDefault()
      void toggleStack(it)
      return true
    }
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
  const firstTotal = result.data?.pages[0]?.total ?? items.length
  useEffect(() => {
    if (!result.isSuccess || onceSeen('visited')) return
    markOnce('visited')
    if (isUnfiledView(query) && firstTotal === 0 && !query.q && window.location.pathname === '/')
      setQuery({ ...emptyQuery, order: query.order })
  }, [result.isSuccess]) // eslint-disable-line react-hooks/exhaustive-deps

  // a folder-limited reader can't see Unfiled items (they're in no
  // folder): show it everything it can see instead
  useEffect(() => {
    if (caps.restricted && (isUnfiledView(query) || query.deleted)) setQuery({ ...emptyQuery, order: query.order })
  }, [caps.restricted, query]) // eslint-disable-line react-hooks/exhaustive-deps

  const folderDescription =
    query.folder && query.folder !== 'none' && !smartId && !query.deleted
      ? (folders ?? []).find((f) => f.id === query.folder)?.description?.trim() || ''
      : ''

  // more like this: the item it started from (not in the results)
  const similarTo = useItem(query.similar, items)
  const personName = usePersonName(query.person)
  const smartName = smartId ? (smartFolders ?? []).find((s) => s.id === smartId)?.name : null
  const searchName = searchTitle(query, { item: () => similarTo?.filename, person: () => personName ?? undefined })
  const title = searchName ?? viewTitle(query, { folder: (id) => folderNames.get(id), smart: smartName })
  // a folder or tag page gets the large title with a small eyebrow above
  // it (B7): the parent folder's name, or what the page is
  const folderPage = !searchName && !smartId && !query.deleted && !!query.folder && query.folder !== 'none'
  const tagPage =
    !searchName && !smartId && !query.deleted && !query.folder && !query.view && query.tags.length === 1
  const parentId = folderPage ? (folders ?? []).find((f) => f.id === query.folder)?.parent_id : null
  const eyebrow = folderPage ? ((parentId && folderNames.get(parentId)) || 'Folder') : tagPage ? 'Tag' : null
  const total = result.data?.pages[0]?.total ?? items.length
  const countText = gridCountText(total, sidebarCount(query, { smart: !!smartId, stats, folders }))

  // the header stays while a new list loads, so the view controls in it
  // keep focus when they change the query (B2)
  const head = (
    <div className="grid-meta">
      <div className="grid-heading">
        {eyebrow && <span className="title-eyebrow">{eyebrow}</span>}
        <div className="grid-title-row">
          <h2 className={`grid-title ${eyebrow ? 'page-title' : 'view-title'}`}>{tagPage ? query.tags[0] : title}</h2>
          {!result.isLoading && !result.isError && (
            <span className="title-count" role="status">
              {countText}
              {/* the bulk bar shows the selection; screen readers hear it here */}
              {selection.size > 1 && <span className="sr-only">, {selection.size} selected</span>}
            </span>
          )}
          <span className="grid-refreshing" aria-hidden="true">
            {result.isFetching && !result.isLoading ? 'Refreshing…' : ''}
          </span>
        </div>
      </div>
      {!mobile && <ViewControls />}
      <SearchNote />
      <KindBar />
      {folderDescription && <p className="view-subtitle folder-description">{folderDescription}</p>}
      {query.fixity === 'failed' && (
        <p className="view-subtitle">
          These originals no longer match the fingerprint (SHA-256) Kist took when they were added: the file on disk
          changed or vanished. Restore each from a backup: Settings, Archive health lists the fingerprint to look for.
        </p>
      )}
      {isUnfiledView(query) && items.length > 0 && (
        <p className="view-subtitle">
          Items with no folder and no tag. Tag or file them to clear this list; they stay in All items.
        </p>
      )}
    </div>
  )

  if (result.isLoading)
    return (
      <main className="grid-area">
        {head}
        <div className="grid-status" role="status">
          Loading library…
        </div>
      </main>
    )
  const status = (result.error as { status?: number } | null)?.status
  if (result.isError && rankedErrorText(status, query))
    return (
      <main className="grid-area">
        {head}
        <RankedError status={status} />
      </main>
    )
  if (result.isError)
    return (
      <main className="grid-area">
        {head}
        <div className="grid-status">
          <div role="alert" className="error">
            Failed to load: {describeError(result.error)}
          </div>
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
      {head}
      <span id={hintId} className="sr-only">
        Arrow keys move and select, Space adds or removes one item, Shift with arrows extends the selection,
        Control with arrows moves without selecting, Enter opens full screen, S opens or closes a stack, C compares and N surveys the selection
        {mobile ? '' : ', Delete moves to Trash, the context menu key shows More like this'}.
      </span>
      <div
        className={`grid-scroll${scrubbing ? ' scrubbed' : ''}${ratingsShown(query) ? ' ratings-shown' : ''}`}
        ref={attachScroll}
        role={items.length ? 'listbox' : undefined}
        aria-multiselectable={items.length ? 'true' : undefined}
        aria-label={items.length ? `${title}, ${countText}` : undefined}
        aria-describedby={items.length ? hintId : undefined}
        onKeyDown={(e) => {
          if (!(e.target as HTMLElement).closest('[role=option]')) return
          if (onGridKey(e)) e.stopPropagation()
        }}
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) clearSelection()
        }}
      >
        <div ref={contentRef} role="presentation" style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
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
                    height={t.height ?? rows[row.index].height}
                    left={t.x}
                    top={t.y}
                    selected={selection.has(item.id)}
                    tabbable={item.id === tabStop}
                    onPointerDown={handlePointerDown}
                    onDoubleClick={openPreview}
                    onDragStart={handleDragStart}
                    onFocus={onTileFocus}
                    draggable={!mobile}
                    member={memberIds.has(item.id)}
                    stacked={
                      !!item.stack_id && (item.stack_count ?? 0) > 1 && !memberIds.has(item.id) && !expanded.has(item.stack_id)
                    }
                    matchBadge={showMatchBadge(item.match?.kind, query)}
                    extraLabel={tileExtra(item, memberIds.has(item.id), !!item.stack_id && expanded.has(item.stack_id), query)}
                    onStackClick={toggleStack}
                    onContextMenu={mobile ? undefined : onTileContextMenu}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      {empty && stats?.total_items === 0 && !query.deleted ? (
        <div className="grid-empty first">
          <FirstRun />
        </div>
      ) : empty && (
        <EmptyState text={empty.text} hint={empty.hint}>
          {empty.action === 'everywhere' && (
            <button onClick={() => setQuery(everywhere(query))}>Search everything</button>
          )}
          {empty.action === 'all' && (
            <button onClick={() => setQuery({ ...emptyQuery, order: query.order })}>Show all items</button>
          )}
        </EmptyState>
      )}
      {scrub.model && scrollEl && items.length > 0 && (
        <Scrubber
          model={scrub.model}
          scrollEl={scrollEl}
          positionAt={scrub.positionAt}
          onScrub={(pos) => {
            if (scrollEl) scrollEl.scrollTop = scrub.offsetOf(pos)
          }}
          onScrubEnd={scrub.goTo}
          mobile={mobile}
          layoutKey={`${scrub.padStart}|${items.length}|${rows.length}`}
        />
      )}
      {!mobile && <BulkBar />}
      {ctxMenu && <ItemContextMenu item={ctxMenu.item} x={ctxMenu.x} y={ctxMenu.y} onClose={closeCtx} />}
    </main>
  )
}
