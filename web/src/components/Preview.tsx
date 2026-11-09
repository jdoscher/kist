import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fileUrl, thumbUrl, type Item } from '../api'
import { swipeDirection } from '../mobile'
import { fullPreviewUrl, pdfFrameProps, previewBoxPx } from '../preview'
import { useCurrentItems, useItem } from '../data'
import { useAppState } from '../state'
import { useFocusInOut, useFocusTrap, useInertBackground } from '../useInert'
import { useLayer, useShortcut } from '../useShortcuts'
import { tileDomId } from './Grid'

/** Show the 1024 thumb at once, then swap in the sharper copy the screen
 * actually needs (preview.ts: usually the 2048 WebP, not the original). */
function ProgressiveImage({ item, mobile }: { item: Item; mobile: boolean }) {
  const [src, setSrc] = useState(thumbUrl(item, 1024) ?? '')

  useEffect(() => {
    const fullUrl = fullPreviewUrl(item, ...previewBoxPx(mobile))
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

  // touch: swipe left/right for the next/previous item, down to close
  const touch = useRef<{ x: number; y: number; n: number } | null>(null)

  // a modal dialog: the page behind is inert, focus starts on Close and
  // goes back to the tile of the item you were looking at last
  const open = !!item
  const overlayRef = useRef<HTMLDivElement>(null)
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

  const onTouchStart = (e: React.TouchEvent) => {
    if ((e.target as HTMLElement).closest('video')) return // scrubbing
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

  return (
    <div
      ref={overlayRef}
      className="preview-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview: ${item.filename}`}
      onClick={() => setPreviewId(null)}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <button ref={closeRef} className="preview-close" aria-label="Close preview" onClick={() => setPreviewId(null)}>
        <span aria-hidden="true">✕</span>
      </button>

      <button
        className="preview-nav prev"
        aria-label="Previous item"
        disabled={idx <= 0}
        onClick={(e) => {
          e.stopPropagation()
          go(-1)
        }}
      >
        <span aria-hidden="true">‹</span>
      </button>

      <figure onClick={(e) => e.stopPropagation()}>
        {item.kind === 'video' ? (
          <video src={fileUrl(item)} controls autoPlay />
        ) : item.kind === 'pdf' ? (
          <PdfFrame item={item} />
        ) : item.has_thumb && item.kind === 'image' && item.ext !== 'svg' ? (
          <ProgressiveImage key={item.id} item={item} mobile={mobile} />
        ) : item.kind === 'image' ? (
          <img src={fileUrl(item)} alt={item.filename} />
        ) : (
          <div className="preview-fallback">
            <p>No preview for .{item.ext}</p>
          </div>
        )}
        <figcaption aria-live="polite" aria-atomic="true">
          <span className="fname">{item.filename}</span>
          <span className="meta">
            {item.width && item.height ? `${item.width}×${item.height}` : ''}
            {idx >= 0 ? `${item.width && item.height ? ' · ' : ''}${idx + 1} / ${items.length}` : ''}
          </span>
          <a href={fileUrl(item)} target="_blank" rel="noreferrer">
            Open original <span aria-hidden="true">↗</span>
            <span className="sr-only"> (new tab)</span>
          </a>
        </figcaption>
      </figure>

      <button
        className="preview-nav next"
        aria-label="Next item"
        disabled={idx < 0 || idx >= items.length - 1}
        onClick={(e) => {
          e.stopPropagation()
          go(1)
        }}
      >
        <span aria-hidden="true">›</span>
      </button>
    </div>
  )
}
