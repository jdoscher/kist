// Milestone 4 and 5 filters on GET /api/items (and timeline, on-this-day,
// map): provenance (ai_generated, license, source_status) and media (date
// taken; date_after/_before: the timeline's date, taken else added; camera, lens, place, GPS, vertical, audio, duration, a map box).
// They ride in the Query as optional flat fields, so smart folders save
// them under the same names the server uses. Pure, tested in
// tests/m45.test.ts.

import type { Query } from '../api.ts'

export const M45_FILTER_KEYS = [
  'taken_after',
  'taken_before',
  'date_after',
  'date_before',
  'camera',
  'lens',
  'place',
  'has_gps',
  'vertical',
  'has_audio',
  'duration_min',
  'duration_max',
  'bbox',
  'ai_generated',
  'license',
  'source_status',
] as const
export type M45FilterKey = (typeof M45_FILTER_KEYS)[number]
export type M45Filters = Partial<Record<M45FilterKey, string | null>>

const BOOLS: M45FilterKey[] = ['has_gps', 'vertical', 'has_audio']
const NUMS: M45FilterKey[] = ['duration_min', 'duration_max']
const DATES: M45FilterKey[] = ['taken_after', 'taken_before', 'date_after', 'date_before']
const STATUSES = ['ok', 'gone', 'moved', 'error', 'unchecked']
const AI = ['true', 'false', 'unknown']

/** One filter value as the server takes it, or null when it isn't valid
 * (an unknown value in a URL is ignored, never an error). */
export function cleanFilter(key: M45FilterKey, raw: unknown): string | null {
  if (raw === null || raw === undefined) return null
  const v = String(raw).trim()
  if (!v) return null
  if (BOOLS.includes(key)) return v === 'true' || v === '1' ? 'true' : v === 'false' || v === '0' ? 'false' : null
  if (NUMS.includes(key)) {
    const n = Number(v)
    return Number.isFinite(n) && n >= 0 ? String(n) : null
  }
  if (DATES.includes(key)) return /^\d{4}-\d{2}-\d{2}(T[\d:.]+(Z|[+-]\d{2}:?\d{2})?)?$/.test(v) ? v : null
  if (key === 'bbox') return parseBbox(v) ? v : null
  if (key === 'source_status') return STATUSES.includes(v) ? v : null
  if (key === 'ai_generated') return AI.includes(v) ? v : null
  return v.slice(0, 200)
}

export function parseBbox(v: string): [number, number, number, number] | null {
  const parts = v.split(',').map(Number)
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return null
  const [w, s, e, n] = parts
  if (w < -180 || w > 180 || e < -180 || e > 180 || s < -90 || n > 90 || s > n) return null
  return [w, s, e, n]
}

/** A bbox string with a little padding (a cluster of one is a point). */
export function bboxString(b: [number, number, number, number], pad = 0.0005): string {
  const r = (x: number) => Math.round(x * 1e6) / 1e6
  const [w, s, e, n] = b
  return [
    r(Math.max(-180, w - pad)),
    r(Math.max(-90, s - pad)),
    r(Math.min(180, e + pad)),
    r(Math.min(90, n + pad)),
  ].join(',')
}

export function m45Filters(q: Partial<Query>): M45Filters {
  const out: M45Filters = {}
  const src = q as Record<string, unknown>
  for (const k of M45_FILTER_KEYS) {
    const v = cleanFilter(k, src[k])
    if (v !== null) out[k] = v
  }
  return out
}

export function hasM45Filters(q: Partial<Query>): boolean {
  return Object.keys(m45Filters(q)).length > 0
}

/** Adds the filters to request parameters (GET /api/items names). */
export function addM45Params(q: Partial<Query>, p: URLSearchParams): URLSearchParams {
  for (const [k, v] of Object.entries(m45Filters(q))) if (v !== null && v !== undefined) p.set(k, v)
  return p
}

/** The URL uses the same names as the API. */
export function m45FromSearch(s: URLSearchParams): M45Filters {
  const out: M45Filters = {}
  for (const k of M45_FILTER_KEYS) {
    const v = cleanFilter(k, s.get(k))
    if (v !== null) out[k] = v
  }
  return out
}

/** Every key, null where unset: spread into setQuery to clear the rest. */
export function clearedM45(): M45Filters {
  return Object.fromEntries(M45_FILTER_KEYS.map((k) => [k, null])) as M45Filters
}

const dateText = (v: string) => v.slice(0, 10)

/** A short label for a filter chip. */
export function filterChipLabel(key: M45FilterKey, v: string): string {
  switch (key) {
    case 'taken_after':
      return `Taken from ${dateText(v)}`
    case 'taken_before':
      return `Taken until ${dateText(v)}`
    case 'date_after':
      return `From ${dateText(v)}`
    case 'date_before':
      return `Until ${dateText(v)}`
    case 'camera':
      return `Camera: ${v}`
    case 'lens':
      return `Lens: ${v}`
    case 'place':
      return `Place: ${v}`
    case 'has_gps':
      return v === 'true' ? 'Has location' : 'No location'
    case 'vertical':
      return v === 'true' ? 'Vertical' : 'Not vertical'
    case 'has_audio':
      return v === 'true' ? 'Has sound' : 'Silent'
    case 'duration_min':
      return `At least ${v} s`
    case 'duration_max':
      return `At most ${v} s`
    case 'bbox':
      return 'Map area'
    case 'ai_generated':
      return v === 'true' ? 'AI-generated' : v === 'false' ? 'Not AI-generated' : 'AI unknown'
    case 'license':
      return v === 'none' ? 'No licence' : `Licence: ${v}`
    case 'source_status':
      return `Source ${v}`
  }
}
