import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fileUrl, thumbUrl, type Item } from '../api'
import { swipeDirection } from '../mobile'
import { fullPreviewUrl, previewBoxPx } from '../preview'
import { flattenItems, useAppState, useItem, useItems } from '../state'

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

export function Preview() {
  const { query, previewId, setPreviewId, selectOnly, mobile } = useAppState()
  const result = useItems(query)
  const items = useMemo(() => flattenItems(result.data?.pages), [result.data])
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
    if (!item) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPreviewId(null)
      else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault()
        go(1)
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault()
        go(-1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [item, go, setPreviewId])

  // touch: swipe left/right for the next/previous item, down to close
  const touch = useRef<{ x: number; y: number; n: number } | null>(null)

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
      className="preview-overlay"
      onClick={() => setPreviewId(null)}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <button
        className="preview-nav prev"
        disabled={idx <= 0}
        onClick={(e) => {
          e.stopPropagation()
          go(-1)
        }}
      >
        ‹
      </button>

      <figure onClick={(e) => e.stopPropagation()}>
        {item.kind === 'video' ? (
          <video src={fileUrl(item)} controls autoPlay />
        ) : item.kind === 'pdf' ? (
          <iframe src={fileUrl(item)} title={item.filename} />
        ) : item.has_thumb && item.kind === 'image' && item.ext !== 'svg' ? (
          <ProgressiveImage key={item.id} item={item} mobile={mobile} />
        ) : item.kind === 'image' ? (
          <img src={fileUrl(item)} alt={item.filename} />
        ) : (
          <div className="preview-fallback">
            <p>No preview for .{item.ext}</p>
          </div>
        )}
        <figcaption>
          <span className="fname">{item.filename}</span>
          <span className="meta">
            {item.width && item.height ? `${item.width}×${item.height}` : ''}
            {idx >= 0 ? `${item.width && item.height ? ' · ' : ''}${idx + 1} / ${items.length}` : ''}
          </span>
          <a href={fileUrl(item)} target="_blank" rel="noreferrer">
            Open original ↗
          </a>
        </figcaption>
      </figure>

      <button
        className="preview-nav next"
        disabled={idx < 0 || idx >= items.length - 1}
        onClick={(e) => {
          e.stopPropagation()
          go(1)
        }}
      >
        ›
      </button>

      <button className="preview-close" onClick={() => setPreviewId(null)}>
        ✕
      </button>
    </div>
  )
}
