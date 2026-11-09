// What a tile shows while its thumbnail loads: the item's ThumbHash (a
// ~25-byte blurred preview) when the server sent one, else its first
// dominant colour, else nothing (the theme's neutral tile colour).
// Tested in tests/placeholder.test.ts.

import { thumbHashToDataURL } from 'thumbhash'

export interface PlaceholderSource {
  thumbhash?: string | null
  dominant_colors?: { hex: string; pct: number }[] | null
}

export type Placeholder = { kind: 'thumbhash'; url: string } | { kind: 'colour'; hex: string } | null

const HEX = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i

/** "#abc" / "aabbcc" -> "#aabbcc"; anything else -> null. */
export function normalizeHex(v: unknown): string | null {
  if (typeof v !== 'string') return null
  const m = HEX.exec(v.trim())
  if (!m) return null
  const h = m[1].length === 3 ? [...m[1]].map((c) => c + c).join('') : m[1]
  return `#${h.toLowerCase()}`
}

function base64ToBytes(b64: string): Uint8Array | null {
  try {
    const bin = atob(b64.replace(/-/g, '+').replace(/_/g, '/'))
    const out = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
    return out
  } catch {
    return null
  }
}

// decoding builds a small PNG, so remember the last few thousand
const cache = new Map<string, string | null>()
const CACHE_MAX = 3000

/** A data: URL for a base64 ThumbHash, or null when it can't be decoded. */
export function thumbhashUrl(b64: string): string | null {
  const hit = cache.get(b64)
  if (hit !== undefined) return hit
  let url: string | null = null
  const bytes = base64ToBytes(b64)
  // a ThumbHash is at least 5 header bytes; garbage throws or is too short
  if (bytes && bytes.length >= 5) {
    try {
      url = thumbHashToDataURL(bytes)
    } catch {
      url = null
    }
  }
  if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string)
  cache.set(b64, url)
  return url
}

export function placeholderFor(item: PlaceholderSource): Placeholder {
  if (item.thumbhash) {
    const url = thumbhashUrl(item.thumbhash)
    if (url) return { kind: 'thumbhash', url }
  }
  const hex = normalizeHex(item.dominant_colors?.[0]?.hex)
  if (hex) return { kind: 'colour', hex }
  return null
}

/** Inline style for the tile box. */
export function placeholderStyle(p: Placeholder): { backgroundColor?: string; backgroundImage?: string } {
  if (!p) return {}
  if (p.kind === 'colour') return { backgroundColor: p.hex }
  return { backgroundImage: `url("${p.url}")` }
}
