// Display formatting shared by every component (no React; tests/format.test.ts).
// One rule per kind of value, so the same number reads the same everywhere.

const UNITS = ['KB', 'MB', 'GB', 'TB'] as const

/**
 * Bytes in binary units: one decimal below 10 ("1.2 GB", "9.9 MB"), none at
 * 10 and above ("12 GB", "340 KB"), plain bytes under 1 KB.
 */
export function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n < 1024) return `${Math.max(0, Math.round(n || 0))} B`
  let v = n
  let unit = -1
  while (v >= 1024 && unit < UNITS.length - 1) {
    v /= 1024
    unit++
  }
  // 9.96 rounds to "10.0": switch to the no-decimal form instead
  const text = v < 9.95 ? v.toFixed(1) : String(Math.round(v))
  if (text === '1024' && unit < UNITS.length - 1) return `1.0 ${UNITS[unit + 1]}`
  return `${text} ${UNITS[unit]}`
}

/** A media duration as m:ss, or h:mm:ss from one hour up. */
export function fmtDuration(seconds: number): string {
  const total = Math.max(0, Math.round(Number.isFinite(seconds) ? seconds : 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

/**
 * A stored source URL that is safe to render as a link: absolute http(s)
 * only. Anything else (a bad string, javascript:, a relative path) gives
 * null, and the caller shows it as plain text. Never throws.
 */
export function linkableUrl(raw: string | null | undefined): { href: string; host: string } | null {
  if (!raw) return null
  try {
    const u = new URL(raw)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
    return { href: u.href, host: u.hostname }
  } catch {
    return null
  }
}
