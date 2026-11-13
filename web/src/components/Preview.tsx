import { ChevronLeft, ChevronRight, ExternalLink, X } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fileUrl, thumbUrl, type Item } from '../api'
import { swipeDirection } from '../mobile'
import { fullPreviewUrl, pdfFrameProps, previewBoxPx, previewMeta, splitName } from '../preview'
import { useCaps, useCurrentItems, useItem } from '../data'
import { useAppState } from '../state'
import { useFocusInOut, useFocusTrap, useInertBackground } from '../useInert'
import { useLayer, useShortcut } from '../useShortcuts'
import { tileDomId } from './Grid'
import { MoreLikeThisButton } from './ItemAI'
import { CutoutButton } from './cutouts/Cutouts' // F7
import { cutoutPreviewUrl, isCutout } from '../cutouts' // F7: cut-outs show transparent
import { PhoneEditBar } from './PhoneEdit'
import { hasMediaView, MediaView } from './m45/lazy'
import { useViewAidKeys, useViewAids, ViewAidsBadge, ViewAidsFrame, ViewAidsMenu } from './viewaids/ViewAids'
import { DEFAULT_AIDS } from '../viewAids'

// notes on the image (Milestone 3) load apart from the first page, and are
// fetched once the app is idle so opening a preview doesn't wait for them
const loadAnnotations = () => import('./m3/Annotations')
const Annotated = lazy(() => loadAnnotations().then((m) => ({ default: m.Annotated })))

/** Show the 1024 thumb at once, then swap in the sharper copy the screen
 * actually needs (preview.ts: usually the 2048 WebP, not the original). */
function ProgressiveImage({ item, mobile }: { item: Item; mobile: boolean }) {
  const [src, setSrc] = useState(isCutout(item) ? cutoutPreviewUrl(item) : (thumbUrl(item, 1024) ?? ''))

  useEffect(() => {
    const fullUrl = isCutout(item) ? cutoutPreviewUrl(item, 2048) : fullPreviewUrl(item, ...previewBoxPx(mobile))
    if (!fullUrl) return
    const full = new Image()
    full.onload = () => setSrc(full.src)
    full.src = fullUrl
    return () => {
      full.onload = null
    }
  }, [item, mobile])

  return <img src={src} alt={item.filename} />
}

/** PDFs: see pdfFrameProps in preview.ts for why the frame has no sandbox. */
function PdfFrame({ item }: { item: Item }) {
  return <iframe src={fileUrl(item)} title={`PDF: ${item.filename}`} {...pdfFrameProps()} />
}

export function Preview() {
  const { previewId, setPreviewId, selectOnly, mobile } = useAppState()
  const { canEdit } = useCaps()
  // phone editing (Milestone 6): stars, tags, Move to…, Trash
  const phoneEdit = mobile && canEdit
  const { result, items } = useCurrentItems()
  const idx = useMemo(() => items.findIndex((i) => i.id === previewId), [items, previewId])
  // an item opened from a link may not be in the loaded grid: show it alone
  const linked = useItem(previewId, items)
  const item = idx >= 0 ? items[idx] : (linked ?? null)

  const go = useCallback(
    (delta: number) => {
      if (idx < 0) return
      const next = idx + delta
      if (next >= 0 && next < items.length) {
        setPreviewId(items[next].id)
        selectOnly(items[next].id)
        if (next >= items.length - 10 && result.hasNextPage && !result.isFetchingNextPage) {
          result.fetchNextPage()
        }
      }
    },
    [idx, items, setPreviewId, selectOnly, result],
  )

  useEffect(() => {
    const idle = window.requestIdleCallback ?? ((fn: () => void) => window.setTimeout(fn, 2000))
    idle(() => void loadAnnotations())
  }, [])

  // while open, the preview is the keyboard's top layer: grid keys
  // (including Delete) stand down until it closes
  useLayer('preview', previewId !== null)
  useShortcut(
    'preview',
    (e) => {
      if (e.key === 'Escape') setPreviewId(null)
      else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault()
        go(1)
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault()
        go(-1)
      } else return false
      return true
    },
    !!item,
  )
  // F5 viewing aids (G V H L T 1): images only; after the keys above so
  // they're offered first, and Escape leaves 100 % / tile before closing
  const [aids, setAids] = useViewAids()
  const aidsOk = !!item && item.kind === 'image' && !hasMediaView(item)
  useViewAidKeys('preview', aidsOk)

  // touch: swipe left/right for the next/previous item, down to close
  const touch = useRef<{ x: number; y: number; n: number } | null>(null)

  // a modal dialog: the page behind is inert, focus starts on Close and
  // goes back to the tile of the item you were looking at last
  const open = !!item
  const overlayRef = useRef<HTMLDivElement>(null)
  const [toolbar, setToolbar] = useState<HTMLDivElement | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const lastId = useRef(previewId)
  if (previewId) lastId.current = previewId
  useInertBackground(overlayRef, open)
  useFocusTrap(overlayRef, open)
  useFocusInOut(overlayRef, open, {
    initial: () => closeRef.current,
    restore: () => (lastId.current ? document.getElementById(tileDomId(lastId.current)) : null),
  })

  if (!item) return null

  const name = splitName(item.filename)
  const meta = previewMeta(item.width, item.height, idx, items.length)

  const onTouchStart = (e: React.TouchEvent) => {
    // scrubbing, turning a model, typing a font sample, picking a file
    if ((e.target as HTMLElement).closest('video, audio, model-viewer, .waveform, .font-stage, .bundle-tree, .media-tools, .phone-edit, .va-actual, .va-menu')) return
    const t = e.touches[0]
    touch.current = { x: t.clientX, y: t.clientY, n: e.touches.length }
  }
  const onTouchEnd = (e: React.TouchEvent) => {
    const s = touch.current
    touch.current = null
    if (!s || s.n > 1 || e.touches.length > 0) return // pinch-zoom, not a swipe
    if (window.visualViewport && window.visualViewport.scale > 1.05) return // zoomed in: panning
    const t = e.changedTouches[0]
    const dir = swipeDirection(t.clientX - s.x, t.clientY - s.y)
    if (dir === 'left') go(1)
    else if (dir === 'right') go(-1)
    else if (dir === 'down') setPreviewId(null)
  }

  // the item itself; notes (Annotated) wrap it once their code is here
  const media = (
    <>
      {hasMediaView(item) ? (
        // Milestone 5: video and audio players, SVG, fonts, bundles,
        // design files and 3D (components/m45/MediaView.tsx)
        <MediaView key={item.id} item={item} fallback={<div className="preview-fallback" role="status">Loading…</div>} />
      ) : item.kind === 'pdf' ? (
        <PdfFrame item={item} />
      ) : item.has_thumb && item.kind === 'image' && item.ext !== 'svg' ? (
        <ViewAidsFrame item={item} aids={aids}>
          <ProgressiveImage key={item.id} item={item} mobile={mobile} />
        </ViewAidsFrame>
      ) : item.kind === 'image' ? (
        <ViewAidsFrame item={item} aids={aids}>
          <img src={fileUrl(item)} alt={item.filename} />
        </ViewAidsFrame>
      ) : (
        <div className="preview-fallback">
          <p>No preview for .{item.ext}</p>
        </div>
      )}
    </>
  )

  return (
    <div
      ref={overlayRef}
      className={`preview-overlay${phoneEdit ? ' phone-editing' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={`Preview: ${item.filename}`}
      onClick={() => setPreviewId(null)}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      {/* the top band (B6): Loupe and Notes portal into .preview-tools,
          so the three controls sit in one row off the image */}
      <div className="preview-band" onClick={(e) => e.stopPropagation()}>
        {aidsOk && <ViewAidsMenu aids={aids} setAids={setAids} mobile={mobile} />}
        <div className="preview-tools" ref={setToolbar} />
        <button ref={closeRef} className="preview-close" aria-label="Close preview" title="Close (Esc)" onClick={() => setPreviewId(null)}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      {aidsOk && <ViewAidsBadge aids={aids} onClear={() => setAids({ ...DEFAULT_AIDS, gridN: aids.gridN, tileMode: aids.tileMode })} />}

      <button
        className="preview-nav prev"
        aria-label="Previous item"
        disabled={idx <= 0}
        onClick={(e) => {
          e.stopPropagation()
          go(-1)
        }}
      >
        <ChevronLeft size={36} aria-hidden="true" />
      </button>

      <figure onClick={(e) => e.stopPropagation()}>
        <Suspense fallback={media}>
          <Annotated key={item.id} item={item} toolbar={toolbar}>
            {media}
          </Annotated>
        </Suspense>
        {/* one line in the bottom band: name.ext · 3840 × 2628 px · 1 of 41 */}
        <figcaption aria-live="polite" aria-atomic="true">
          <span className="fname" title={item.filename}>
            <span className="fname-base">{name.base}</span>
            {name.ext && <span className="ext">{name.ext}</span>}
          </span>
          {meta && <span className="meta">{meta}</span>}
          <span className="preview-actions">
            <MoreLikeThisButton item={item} className="preview-more" />
            <CutoutButton item={item} className="preview-cutout" />
            <a href={fileUrl(item)} target="_blank" rel="noreferrer">
              Open original <ExternalLink size={14} aria-hidden="true" />
              <span className="sr-only"> (new tab)</span>
            </a>
          </span>
        </figcaption>
      </figure>

      {phoneEdit && (
        <PhoneEditBar
          key={item.id}
          item={item}
          onTrashed={() => {
            // on to the next item (or the previous one at the end); the
            // last one closes the preview
            const rest = items.filter((i) => i.id !== item.id)
            const next = idx >= 0 ? (rest[idx] ?? rest[idx - 1]) : undefined
            if (next) {
              setPreviewId(next.id)
              selectOnly(next.id)
            } else setPreviewId(null)
          }}
        />
      )}

      <button
        className="preview-nav next"
        aria-label="Next item"
        disabled={idx < 0 || idx >= items.length - 1}
        onClick={(e) => {
          e.stopPropagation()
          go(1)
        }}
      >
        <ChevronRight size={36} aria-hidden="true" />
      </button>
    </div>
  )
}
