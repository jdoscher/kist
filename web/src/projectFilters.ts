// Kist 2.0 F6: the project= and field.<key>= filters, as optional Query
// fields (like m45/filters.ts), in request and URL parameters. Pure;
// tested in tests/projects.test.ts.

import type { Query } from './api.ts'

export interface ProjectFilters {
  /** items filed in this project's folders */
  project?: string | null
  /** custom field filters, {field key: expression} */
  custom_fields?: Record<string, string> | null
}

const KEY_RE = /^[a-z0-9_]{1,40}$/

/** A field name as the filter key the server uses ('Look number' -> 'look_number'). */
export function fieldKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
}

export function projectFilters(q: Partial<Query>): ProjectFilters {
  const out: ProjectFilters = {}
  if (q.project) out.project = q.project
  const cf = cleanFieldFilters(q.custom_fields)
  if (cf) out.custom_fields = cf
  return out
}

function cleanFieldFilters(raw: unknown): Record<string, string> | null {
  if (!raw || typeof raw !== 'object') return null
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (v === null || v === undefined) continue
    const text = String(v).trim().slice(0, 500)
    if (KEY_RE.test(k) && text) out[k] = text
  }
  return Object.keys(out).length ? out : null
}

export function hasProjectFilters(q: Partial<Query>): boolean {
  return Object.keys(projectFilters(q)).length > 0
}

/** Adds them to request (and URL) parameters: project=, field.<key>=. */
export function addProjectParams(q: Partial<Query>, p: URLSearchParams): URLSearchParams {
  const f = projectFilters(q)
  if (f.project) p.set('project', f.project)
  for (const [k, v] of Object.entries(f.custom_fields ?? {})) p.set(`field.${k}`, v)
  return p
}

/** The URL uses the API's names. */
export function projectFromSearch(s: URLSearchParams): ProjectFilters {
  const fields: Record<string, string> = {}
  for (const [k, v] of s.entries()) if (k.startsWith('field.')) fields[k.slice(6)] = v
  return projectFilters({ project: s.get('project') || null, custom_fields: fields })
}

/** Every key, null where unset: spread into setQuery to clear them. */
export function clearedProjectFilters(): ProjectFilters {
  return { project: null, custom_fields: null }
}

/** One field filter changed (null/'' removes it). */
export function withFieldFilter(q: Partial<Query>, key: string, expr: string | null): ProjectFilters['custom_fields'] {
  const next = { ...(q.custom_fields ?? {}) }
  if (expr === null || !expr.trim()) delete next[key]
  else next[key] = expr.trim()
  return Object.keys(next).length ? next : null
}

/** A short chip label for a field filter: "Season: SS27", "Edition ≥ 3". */
export function fieldChipLabel(name: string, expr: string): string {
  if (expr === '*') return `${name}: any`
  if (expr.toLowerCase() === 'none') return `${name}: none`
  const m = /^(>=|<=|>|<)\s*(.+)$/.exec(expr)
  if (m) return `${name} ${{ '>=': '≥', '<=': '≤', '>': '>', '<': '<' }[m[1]]} ${m[2]}`
  if (expr.includes('..')) {
    const [a, b] = expr.split('..')
    if (a && b) return `${name}: ${a}–${b}`
    return a ? `${name} ≥ ${a}` : `${name} ≤ ${b}`
  }
  return `${name}: ${expr.split('|').join(' or ')}`
}

