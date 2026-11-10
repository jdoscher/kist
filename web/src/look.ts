// Which image to show where: the display copy (the 2048 WebP, when the
// item is bigger than the 1024 thumbnail) and the original (for 100%
// views: the loupe, zoomed compare). No React (tests/creative.test.ts).

export interface LookItem {
  id: string
  ext: string
  kind: string
  width: number | null
  height: number | null
  has_thumb: boolean
  preview_rev: number
}

const BROWSER_SHOWS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'svg', 'bmp'])

/** The sharpest copy that's quick to show full screen. */
export function displaySrc(it: LookItem): string {
  if (!it.has_thumb) return originalSrc(it)
  const long = Math.max(it.width ?? 0, it.height ?? 0)
  return `/api/items/${it.id}/thumb?size=${long > 1024 ? 2048 : 1024}&v=${it.preview_rev}`
}

/** The original when a browser can draw it, else the biggest preview
 * (HEIC gets its 4096 copy). */
export function originalSrc(it: LookItem): string {
  if (it.kind === 'image' && BROWSER_SHOWS.has(it.ext.toLowerCase())) return `/api/items/${it.id}/file`
  if (it.ext.toLowerCase() === 'heic' || it.ext.toLowerCase() === 'heif') return `/api/items/${it.id}/thumb?size=4096&v=${it.preview_rev}`
  return `/api/items/${it.id}/thumb?size=2048&v=${it.preview_rev}`
}

/** The loupe's background: the image at 100% (natural size), placed so the
 * point under the pointer (fx, fy: fractions of the shown image) is in the
 * lens's centre. */
export function loupeStyle(
  natW: number,
  natH: number,
  fx: number,
  fy: number,
  lens: number,
): { backgroundSize: string; backgroundPosition: string } {
  return {
    backgroundSize: `${natW}px ${natH}px`,
    backgroundPosition: `${Math.round(lens / 2 - fx * natW)}px ${Math.round(lens / 2 - fy * natH)}px`,
  }
}
