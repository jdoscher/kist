// Kist 2.0 F6: projects, pinned sidebar entries and per-project custom
// fields. Types, the REST calls, and the pure helpers (filters in the URL
// and the items query, pin targets, reordering, field values), tested in
// tests/projects.test.ts.
//
//   /projects            the projects list (active; ?archived=1 for archived)
//   /project/:id         a project's page
//   ?project=:id         items in a project's folders (a grid filter)
//   ?field.<key>=<expr>  a custom field filter (same names as the API)

import type { Item, Query } from './api.ts'
import { json, request, withBatch } from './api.ts'

export type ProjectStatus = 'active' | 'archived'
export type FieldType = 'text' | 'number' | 'date' | 'choice' | 'url'
export const FIELD_TYPES: FieldType[] = ['text', 'number', 'date', 'choice', 'url']
export const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  text: 'Text',
  number: 'Number',
  date: 'Date',
  choice: 'Choice',
  url: 'Link',
}

export interface FieldDef {
  id: string
  name: string
  key: string
  type: FieldType
  choices?: string[]
}

export type MemberKind = 'folder' | 'smart' | 'board' | 'share' | 'show' | string
export const MEMBER_KIND_LABELS: Record<string, string> = {
  folder: 'Folders',
  smart: 'Smart folders',
  board: 'Boards',
  share: 'Share links',
  show: 'Shows',
}
export const MEMBER_KIND_ORDER = ['folder', 'smart', 'board', 'share', 'show']

export interface Member {
  kind: MemberKind
  id: string
  name: string
  missing: boolean
  /** share links: their public path and whether they still work */
  url?: string
  live?: boolean
  /** smart folders: the saved search */
  query?: Partial<Query>
  trashed?: boolean
}

export interface ProjectSummary {
  id: string
  name: string
  client: string
  job_number: string
  phase: string
  notes: string
  status: ProjectStatus
  created_at: string
  modified_at: string
  member_counts: Record<string, number>
  fields: FieldDef[]
}

export interface Project extends ProjectSummary {
  members: Member[]
}

export type ProjectPatch = Partial<Pick<ProjectSummary, 'name' | 'client' | 'job_number' | 'phase' | 'notes' | 'status'>>

export interface FieldIndexEntry {
  key: string
  name: string
  type: FieldType
  choices: string[]
  projects: { id: string; name: string; status: ProjectStatus }[]
}

export type FieldValue = string | number | null

export interface ItemProjectFields {
  project: { id: string; name: string; status: ProjectStatus }
  fields: (FieldDef & { value: FieldValue })[]
}

export type PinKind = 'project' | 'folder' | 'smart' | 'board' | 'person' | 'tag' | 'search'

export interface Pin {
  kind: PinKind
  ref: string
  name: string
  /** search: URL params; smart: its saved query */
  query?: string | Partial<Query>
}

export interface BulkFieldsResult {
  updated: number
  skipped: { id: string; reason: string }[]
  batch_id: string
}

const enc = encodeURIComponent

function send(method: string, body?: unknown): RequestInit {
  return body === undefined ? { method } : { ...json(body), method }
}

export const projectsApi = {
  list: (status: ProjectStatus | 'all' = 'active') => request<ProjectSummary[]>(`/api/projects?status=${status}`),
  get: (id: string) => request<Project>(`/api/projects/${enc(id)}`),
  create: (body: ProjectPatch & { name: string }) => request<Project>('/api/projects', json(body)),
  patch: (id: string, body: ProjectPatch) => request<Project>(`/api/projects/${enc(id)}`, send('PATCH', body)),
  remove: (id: string, batch?: string) =>
    request<{ deleted: string; values_cleared: number }>(`/api/projects/${enc(id)}`, withBatch(send('DELETE'), batch)),
  addMember: (id: string, kind: MemberKind, memberId: string) =>
    request<Project>(`/api/projects/${enc(id)}/members`, json({ kind, id: memberId })),
  removeMember: (id: string, kind: MemberKind, memberId: string) =>
    request<Project>(`/api/projects/${enc(id)}/members/${enc(kind)}/${enc(memberId)}`, send('DELETE')),
  addField: (id: string, body: { name: string; type: FieldType; choices?: string[] }) =>
    request<Project>(`/api/projects/${enc(id)}/fields`, json(body)),
  patchField: (id: string, fieldId: string, body: { name?: string; choices?: string[]; position?: number }) =>
    request<Project>(`/api/projects/${enc(id)}/fields/${enc(fieldId)}`, send('PATCH', body)),
  removeField: (id: string, fieldId: string, batch?: string) =>
    request<Project>(`/api/projects/${enc(id)}/fields/${enc(fieldId)}`, withBatch(send('DELETE'), batch)),
  projectsOf: (kind: MemberKind, memberId: string) =>
    request<ProjectSummary[]>(`/api/projects-of/${enc(kind)}/${enc(memberId)}`),
  fields: () => request<FieldIndexEntry[]>('/api/fields'),
  itemFields: (itemId: string) => request<ItemProjectFields[]>(`/api/items/${enc(itemId)}/fields`),
  setItemFields: (itemId: string, values: Record<string, FieldValue>, batch?: string) =>
    request<{ item: Item; projects: ItemProjectFields[] }>(
      `/api/items/${enc(itemId)}/fields`,
      withBatch(send('PATCH', { values }), batch),
    ),
  bulkFields: (itemIds: string[], values: Record<string, FieldValue>, batch?: string) =>
    request<BulkFieldsResult>('/api/fields/bulk', withBatch(json({ item_ids: itemIds, values }), batch)),
  pins: () => request<Pin[]>('/api/pins'),
  pin: (body: { kind: PinKind; ref?: string; name?: string; query?: string }) => request<Pin[]>('/api/pins', json(body)),
  unpin: (kind: PinKind, ref: string) => request<Pin[]>(`/api/pins?kind=${enc(kind)}&ref=${enc(ref)}`, send('DELETE')),
  reorderPins: (pins: { kind: PinKind; ref: string }[]) => request<Pin[]>('/api/pins/order', send('PUT', { pins })),
}

// the filters live in projectFilters.ts (api.ts imports them)
export * from './projectFilters.ts'

// ---------- field values ----------

/** A typed value as the server takes it; null clears; a string is the
 * error to show. */
export function parseFieldInput(field: FieldDef, raw: string): { value: FieldValue } | { error: string } {
  const text = raw.trim()
  if (!text) return { value: null }
  switch (field.type) {
    case 'number': {
      const n = Number(text.replace(',', '.'))
      return Number.isFinite(n) ? { value: n } : { error: `${field.name} takes a number` }
    }
    case 'date':
      return /^\d{4}-\d{2}-\d{2}$/.test(text) && !Number.isNaN(Date.parse(text))
        ? { value: text }
        : { error: `${field.name} takes a date like 2026-11-12` }
    case 'choice': {
      const hit = (field.choices ?? []).find((c) => c.toLowerCase() === text.toLowerCase())
      return hit ? { value: hit } : { error: `${field.name} is one of: ${(field.choices ?? []).join(', ')}` }
    }
    case 'url':
      return /^https?:\/\/[^\s/]+/i.test(text) ? { value: text } : { error: `${field.name} takes an http(s) link` }
    default:
      return { value: text }
  }
}

export function fieldValueText(value: FieldValue | undefined): string {
  return value === null || value === undefined ? '' : String(value)
}

/** The grid filter that shows items with this value ("Season: SS27"). */
export function filterForValue(field: FieldDef, value: FieldValue): string {
  if (value === null) return 'none'
  if (field.type === 'text' || field.type === 'url') return String(value)
  return String(value)
}

// ---------- pins ----------

/** What the current place would pin, if anything. */
export function pinForPlace(p: {
  query: Partial<Query>
  smartId: string | null
  screen?: { kind: string; id?: string } | null
}): { kind: PinKind; ref: string } | null {
  if (p.screen?.kind === 'project' && p.screen.id) return { kind: 'project', ref: p.screen.id }
  if (p.screen?.kind === 'board' && p.screen.id) return { kind: 'board', ref: p.screen.id }
  if (p.screen) return null
  if (p.smartId) return { kind: 'smart', ref: p.smartId }
  const q = p.query
  if (q.folder && q.folder !== 'none' && !q.deleted) return { kind: 'folder', ref: q.folder }
  if (q.person) return { kind: 'person', ref: q.person }
  if (q.tags?.length === 1) return { kind: 'tag', ref: q.tags[0] }
  return null
}

export function isPinned(pins: Pin[] | undefined, kind: PinKind, ref: string): boolean {
  return !!pins?.some((p) => p.kind === kind && (kind === 'tag' ? p.ref.toLowerCase() === ref.toLowerCase() : p.ref === ref))
}

/** Move one pin to another's place (drag and drop, or Alt+↑/↓). */
export function movePin<T extends { kind: string; ref: string }>(pins: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || from >= pins.length) return pins
  const out = pins.slice()
  const [moved] = out.splice(from, 1)
  out.splice(Math.max(0, Math.min(out.length, to)), 0, moved)
  return out
}

/** A saved-search pin's params as the search part of a URL. */
export function searchPinUrl(query: string): string {
  const qs = query.replace(/^\?/, '')
  const s = new URLSearchParams(qs)
  s.delete('item')
  s.delete('full')
  const rest = s.toString()
  return rest ? `/all?${rest}` : '/all'
}

/** A project's subtitle: client · job number · phase. */
export function projectSubtitle(p: Pick<ProjectSummary, 'client' | 'job_number' | 'phase'>): string {
  return [p.client, p.job_number, p.phase].filter((x) => x && x.trim()).join(' · ')
}

/** Members grouped by kind, in the page's order (unknown kinds last). */
export function groupMembers(members: Member[]): [string, Member[]][] {
  const by = new Map<string, Member[]>()
  for (const m of members) by.set(m.kind, [...(by.get(m.kind) ?? []), m])
  const kinds = [...by.keys()].sort((a, b) => {
    const ia = MEMBER_KIND_ORDER.indexOf(a)
    const ib = MEMBER_KIND_ORDER.indexOf(b)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
  })
  return kinds.map((k) => [k, by.get(k) ?? []])
}
