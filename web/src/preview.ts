// Which image the full-screen preview shows (no React; tests/preview.test.ts).
//
// Measured 2026-11-09: originals run 5-30 MB, while a phone shows ~1170 px.
// The server keeps a 2048 px WebP (~0.5 MB) of every image over 1024 px, so
// the original is only worth loading when the screen is wider than that.

export interface PreviewItem {
  id: string
  ext: string
  width: number | null
  height: number | null
  size_bytes: number
  preview_rev: number
}

// formats every browser renders natively
const WEB_NATIVE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif'])
// formats browsers can't render: the server keeps a full-res display.webp
const NEEDS_DISPLAY_COPY = new Set(['heic', 'heif'])
export const MAX_ORIGINAL_BYTES = 40 * 1024 * 1024
export const SCREEN_COPY = 2048

const thumb = (it: PreviewItem, size: number) =>
  `/api/items/${it.id}/thumb?size=${size}&v=${it.preview_rev}`

/** The long side, in device pixels, the image is shown at when fitted into a
 * box of boxW x boxH device pixels (never more than its own size). */
export function shownLongSide(it: PreviewItem, boxW: number, boxH: number): number {
  const w = it.width ?? 0
  const h = it.height ?? 0
  if (!w || !h) return 0
  const scale = Math.min(1, boxW / w, boxH / h)
  return Math.round(Math.max(w, h) * scale)
}

/**
 * The sharper image to swap in after the 1024 thumbnail, or null when the
 * thumbnail already shows everything. boxW/boxH: the space the preview has,
 * in device pixels.
 */
export function fullPreviewUrl(it: PreviewItem, boxW: number, boxH: number): string | null {
  const long = Math.max(it.width ?? 0, it.height ?? 0)
  if (long <= 1024) return null
  const ext = it.ext.toLowerCase()
  const needed = shownLongSide(it, boxW, boxH)
  if (needed <= 1024) return null
  if (needed <= SCREEN_COPY) return thumb(it, SCREEN_COPY)
  // a big, high-density screen: worth the full resolution
  if (NEEDS_DISPLAY_COPY.has(ext)) return thumb(it, 4096)
  if (WEB_NATIVE.has(ext) && it.size_bytes <= MAX_ORIGINAL_BYTES) return `/api/items/${it.id}/file`
  return thumb(it, SCREEN_COPY)
}

/** The preview's box in device pixels (matches .preview-overlay img limits). */
export function previewBoxPx(mobile: boolean): [number, number] {
  const dpr = window.devicePixelRatio || 1
  const w = mobile ? window.innerWidth : window.innerWidth - 140
  const h = window.innerHeight - (mobile ? 120 : 90)
  return [Math.round(w * dpr), Math.round(h * dpr)]
}
