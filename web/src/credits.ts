// F4: credits and rights, the AI-made flag and the print check — the pure
// parts (no React, no fetch; tests/credits.test.ts). The server side is
// kist/services/credits.py; the API calls are in creditsApi.ts.
//
// The filters ride in the Query as flat fields beside the M4/M5 ones
// (m45/filters.ts spreads CREDIT_FILTER_KEYS into its list), so the URL,
// smart folders, chips and the timeline/map get them for free.

export const RIGHTS_STATUSES = ['ref_only', 'comp', 'licensed', 'cleared'] as const
export type RightsStatus = (typeof RIGHTS_STATUSES)[number]

export const RIGHTS_TEXT: Record<RightsStatus, string> = {
  ref_only: 'Ref only',
  comp: 'Comp',
  licensed: 'Licensed',
  cleared: 'Cleared',
}

/** One line on what each status means (the select's hint). */
export const RIGHTS_HINT: Record<RightsStatus, string> = {
  ref_only: 'Reference only: not for use in finished work',
  comp: 'A comp / FPO: may be shown in a layout, not published',
  licensed: 'Licensed for use (see the licence)',
  cleared: 'Cleared for the client or for publication',
}

/** What may leave the building without a warning. */
export const CLEARED: ReadonlySet<string> = new Set(['licensed', 'cleared'])

export function isRightsStatus(v: unknown): v is RightsStatus {
  return typeof v === 'string' && (RIGHTS_STATUSES as readonly string[]).includes(v)
}

export function rightsText(v: unknown): string {
  return isRightsStatus(v) ? RIGHTS_TEXT[v] : 'Not set'
}

// ---------- filters ----------

export const CREDIT_FILTER_KEYS = ['rights_status', 'needs_credit', 'print_min', 'print_max'] as const
export type CreditFilterKey = (typeof CREDIT_FILTER_KEYS)[number]

const RIGHTS_FILTER_VALUES = [...RIGHTS_STATUSES, 'none', 'uncleared']
export const PAPERS = ['A5', 'A4', 'A3', 'A2', 'A1', 'Letter', 'Tabloid'] as const
const SIZE_RE = /^\d+(\.\d+)?\s*(in|cm|mm)?$/i

/** A print size the server takes ('8in', '20cm', 'A4'), or null. */
export function cleanPrintSize(v: string): string | null {
  const t = v.trim()
  const paper = PAPERS.find((p) => p.toLowerCase() === t.toLowerCase())
  if (paper) return paper
  if (!SIZE_RE.test(t)) return null
  const n = parseFloat(t)
  return n > 0 && n <= 1000 ? t.replace(/\s+/g, '').toLowerCase() : null
}

/** One credits filter value, or null when it isn't valid (ignored, never an error). */
export function cleanCreditFilter(key: CreditFilterKey, raw: string): string | null {
  const v = raw.trim()
  if (!v) return null
  if (key === 'needs_credit') return v === 'true' || v === '1' ? 'true' : v === 'false' || v === '0' ? 'false' : null
  if (key === 'rights_status') {
    const parts = v
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean)
    return parts.length && parts.every((p) => RIGHTS_FILTER_VALUES.includes(p)) ? parts.join(',') : null
  }
  return cleanPrintSize(v)
}

export function creditChipLabel(key: CreditFilterKey, v: string): string {
  switch (key) {
    case 'needs_credit':
      return v === 'true' ? 'Needs a credit' : 'Has a credit'
    case 'rights_status':
      return v
        .split(',')
        .map((s) => (s === 'none' ? 'No rights status' : s === 'uncleared' ? 'Not cleared' : rightsText(s)))
        .join(' or ')
    case 'print_min':
      return `Prints ${v} at 300 dpi`
    case 'print_max':
      return `Too small for ${v}`
  }
}

/** The "Needs a credit" view: a plain filter, so it combines with folders. */
export const NEEDS_CREDIT = { needs_credit: 'true' } as const

export function isNeedsCreditView(q: Record<string, unknown>): boolean {
  return q.needs_credit === 'true'
}

// ---------- the item ----------

type Prov = Record<string, unknown> | null | undefined

/** 'Creator / Credit' like the server's credits.credit_line. */
export function creditLine(p: Prov): string | null {
  const creator = String(p?.creator ?? '').trim()
  const credit = String(p?.credit ?? '').trim()
  if (creator && credit && !credit.toLowerCase().includes(creator.toLowerCase())) return `${creator} / ${credit}`
  return credit || creator || null
}

export function needsCredit(p: Prov): boolean {
  return !creditLine(p) && p?.credit_exempt !== true
}

/** AI-made from metadata (or the user's say-so): a flag, never a filter-out. */
export function isAiMade(p: Prov): boolean {
  return p?.ai_generated === true
}

/** Why Kist thinks so, in words. */
export function aiReason(p: Prov): string | null {
  if (!isAiMade(p)) return null
  const gen = typeof p?.ai_generator === 'string' ? p.ai_generator : null
  if (p?.c2pa) return gen ? `${gen} (Content Credentials)` : 'Content Credentials (C2PA)'
  if (typeof p?.digital_source_type === 'string') return gen ? `${gen} (IPTC)` : 'IPTC digital source type'
  if (gen) return `${gen} (named in the file’s metadata)`
  return 'Marked by hand'
}

/** Added to a tile's accessible name. */
export function creditsTileLabel(p: Prov): string {
  return isAiMade(p) ? ', AI-made' : ''
}

// ---------- print ----------

export interface PrintInfo {
  width: number | null
  height: number | null
  sizes: Record<string, { width: { in: number; cm: number }; height: { in: number; cm: number } }>
  fits: Record<string, string[]>
  low_res: boolean | null
  icc: string | null
  colour_space: string | null
  file_dpi: number | null
  untagged: boolean
}

export type Units = 'in' | 'cm'

export function sizeText(info: PrintInfo, dpi: 300 | 150, units: Units): string | null {
  const s = info.sizes[String(dpi)]
  if (!s) return null
  return `${s.width[units].toFixed(1)} × ${s.height[units].toFixed(1)} ${units}`
}

/** The biggest paper it fills, or a plain 'smaller than A5'. */
export function fitsText(info: PrintInfo, dpi: 300 | 150): string {
  const papers = info.fits[String(dpi)] ?? []
  const iso = ['A1', 'A2', 'A3', 'A4', 'A5'].find((p) => papers.includes(p))
  if (iso) return `fills ${iso}`
  return papers.length ? `fills ${papers[papers.length - 1]}` : 'smaller than A5'
}

export function profileText(info: PrintInfo): string {
  if (info.icc) return info.colour_space ? `${info.icc} (${info.colour_space})` : info.icc
  if (info.colour_space === 'CMYK') return 'CMYK, no profile'
  if (info.untagged) return 'None (untagged; shown as sRGB)'
  return info.colour_space ?? 'Unknown'
}

// ---------- clearance ----------

export interface Clearance {
  enabled: boolean
  total: number
  uncleared: number
  by_status: Record<string, number>
  items: { id: string; filename: string; rights_status: string | null; credit_line: string | null }[]
}

/** The warning's one line; null when nothing to say. */
export function clearanceText(c: Clearance | null | undefined): string | null {
  if (!c || !c.enabled || c.uncleared === 0) return null
  const n = c.uncleared
  if (n < c.total) return `${n} of ${c.total} items aren’t marked licensed or cleared.`
  return n === 1 ? 'This item isn’t marked licensed or cleared.' : `None of these ${n} items are marked licensed or cleared.`
}

export function statusBreakdown(c: Clearance): string {
  const order = ['comp', 'ref_only', 'none']
  return order
    .filter((k) => c.by_status[k])
    .map((k) => `${c.by_status[k]} ${k === 'none' ? 'not set' : rightsText(k).toLowerCase()}`)
    .join(', ')
}

// ---------- bulk ----------

export interface BulkCreditsForm {
  creator: string
  credit: string
  license: string
  rights_status: '' | RightsStatus | 'clear'
  credit_exempt: '' | 'yes' | 'no'
}

export const emptyBulkCredits: BulkCreditsForm = { creator: '', credit: '', license: '', rights_status: '', credit_exempt: '' }

/** The POST /api/credits/bulk provenance body: blank fields are left alone. */
export function bulkCreditsPatch(f: BulkCreditsForm): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of ['creator', 'credit', 'license'] as const) if (f[k].trim()) out[k] = f[k].trim()
  if (f.rights_status === 'clear') out.rights_status = null
  else if (f.rights_status) out.rights_status = f.rights_status
  if (f.credit_exempt) out.credit_exempt = f.credit_exempt === 'yes' ? true : null
  return out
}
