import { useCallback, useEffect, useMemo, useState } from 'react'
import { fileUrl, thumbUrl, type Item } from '../api'
import { flattenItems, useAppState, useItems } from '../state'

// formats every browser renders natively — load the original itself
const WEB_NATIVE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif'])
// formats Chrome can't render — the server keeps a full-res display.webp
const NEEDS_DISPLAY_COPY = new Set(['heic', 'heif'])
const MAX_ORIGINAL_BYTES = 40 * 1024 * 1024

/** Show the 1024 thumb at once, then swap in a full-res version once loaded. */
function ProgressiveImage({ item }: { item: Item }) {
  const [src, setSrc] = useState(thumbUrl(item, 1024) ?? '')

  useEffect(() => {
    const ext = item.ext.toLowerCase()
    if (Math.max(item.width ?? 0, item.height ?? 0) <= 1024) return
    let fullUrl: string | null = null
    if (WEB_NATIVE.has(ext) && item.size_bytes <= MAX_ORIGINAL_BYTES) fullUrl = fileUrl(item)
    else if (NEEDS_DISPLAY_COPY.has(ext)) fullUrl = `/api/items/${item.id}/thumb?size=4096`
    if (!fullUrl) return
    const full = new Image()
    full.onload = () => setSrc(full.src)
    full.src = fullUrl
    return () => {
      full.onload = null
    }
  }, [item])

  return <img src={src} alt={item.filename} />
}

export function Preview() {
  const { query, previewId, setPreviewId, selectOnly } = useAppState()
  const result = useItems(query)
  const items = useMemo(() => flattenItems(result.data?.pages), [result.data])
  const idx = useMemo(() => items.findIndex((i) => i.id === previewId), [items, previewId])
  const item = idx >= 0 ? items[idx] : null

  const go = useCallback(
    (delta: number) => {
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

  if (!item) return null

  return (
    <div className="preview-overlay" onClick={() => setPreviewId(null)}>
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
          <ProgressiveImage key={item.id} item={item} />
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
            {item.width && item.height ? `${item.width}×${item.height} · ` : ''}
            {idx + 1} / {items.length}
          </span>
          <a href={fileUrl(item)} target="_blank" rel="noreferrer">
            Open original ↗
          </a>
        </figcaption>
      </figure>

      <button
        className="preview-nav next"
        disabled={idx >= items.length - 1}
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
