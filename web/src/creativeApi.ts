// Milestone 3 endpoints: boards, annotations, versions, templates, bulk
// rename, triage and the command palette. Same request rules as api.ts
// (token, X-Kist-Client, readable errors, X-Kist-Batch for undo).

import { ApiError, getToken, CLIENT_HEADERS, detailText, json, request, withBatch, type Item, type ItemPage } from './api'
import type { Annotation, AnnotationIn } from './annotate'
import type { BoardContent, BoardDoc, BoardPatch, BoardSummary } from './board'
import type { PaletteEntry } from './palette'

export interface TemplateFields {
  tags_add?: string[]
  folder_add?: string[]
  notes_append?: string
  source_url?: string | null
  rating?: number | null
  label?: string | null
  flag?: string | null
  credit?: string | null
  license?: string | null
}

export interface Template {
  id: string
  name: string
  fields: TemplateFields
}

export interface RenameRow {
  id: string
  before: string
  after: string
  changed: boolean
}

export interface TriageNext {
  item: Item | null
  prefetch_ids: string[]
  remaining: number
}

/** The board a 409 answer carries (changed elsewhere), if any. */
export function conflictBoard(e: unknown): BoardDoc | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  const d = (e.data as { detail?: { board?: BoardDoc } } | undefined)?.detail
  return d?.board ?? null
}

const put = (body: unknown): RequestInit => ({ ...json(body), method: 'PUT' })
const patch = (body: unknown): RequestInit => ({ ...json(body), method: 'PATCH' })

export const api3 = {
  // ---------- boards ----------
  listBoards: (state: 'active' | 'trashed' = 'active', q = '') =>
    request<BoardSummary[]>(`/api/boards?state=${state}${q ? `&q=${encodeURIComponent(q)}` : ''}`),
  createBoard: (body: Partial<BoardContent> & { name: string; item_ids?: string[]; layout?: 'grid' | 'masonry' }) =>
    request<BoardDoc>('/api/boards', json(body)),
  getBoard: (id: string) => request<BoardDoc>(`/api/boards/${id}`),
  putBoard: (id: string, base_version: number, content: BoardContent, batch?: string) =>
    request<BoardDoc>(`/api/boards/${id}`, withBatch(put({ ...content, base_version }), batch)),
  patchBoard: (id: string, ops: BoardPatch, batch?: string) => request<BoardDoc>(`/api/boards/${id}`, withBatch(patch(ops), batch)),
  addToBoard: (id: string, item_ids: string[], layout: 'grid' | 'masonry' = 'grid') =>
    request<BoardDoc>(`/api/boards/${id}/items`, json({ item_ids, layout })),
  deleteBoard: (id: string, hard = false) =>
    request<unknown>(`/api/boards/${id}${hard ? '?hard=true' : ''}`, { method: 'DELETE' }),
  restoreBoard: (id: string) => request<BoardDoc>(`/api/boards/${id}/restore`, { method: 'POST' }),
  duplicateBoard: (id: string, name?: string) =>
    request<BoardDoc>(`/api/boards/${id}/duplicate${name ? `?name=${encodeURIComponent(name)}` : ''}`, { method: 'POST' }),
  itemBoards: (itemId: string) => request<BoardSummary[]>(`/api/items/${itemId}/boards`),
  exportUrl: (id: string, format: 'png' | 'pdf' | 'html', scale = 2) =>
    `/api/boards/${id}/export?format=${format}${format === 'html' ? '' : `&scale=${scale}`}`,

  // ---------- items ----------
  /** specific items, in this order (compare, survey, boards) */
  itemsByIds: (ids: string[]) =>
    ids.length ? request<ItemPage>(`/api/items?ids=${ids.map(encodeURIComponent).join(',')}`) : Promise.resolve({ items: [], next_cursor: null, total: 0 }),
  annotations: (itemId: string) => request<Annotation[]>(`/api/items/${itemId}/annotations`),
  addAnnotation: (itemId: string, a: AnnotationIn) => request<Annotation>(`/api/items/${itemId}/annotations`, json(a)),
  patchAnnotation: (itemId: string, id: string, a: Partial<AnnotationIn>) =>
    request<Annotation>(`/api/items/${itemId}/annotations/${id}`, patch(a)),
  deleteAnnotation: (itemId: string, id: string) =>
    request<unknown>(`/api/items/${itemId}/annotations/${id}`, { method: 'DELETE' }),
  versions: (itemId: string) => request<{ items: Item[]; current_id: string }>(`/api/items/${itemId}/versions`),
  rename: (item_ids: string[], pattern: string, dry_run: boolean, start = 1) =>
    request<{ dry_run: boolean; items: RenameRow[]; batch_id: string | null }>(
      '/api/items/rename',
      json({ item_ids, pattern, dry_run, start }),
    ),

  // ---------- templates ----------
  templates: () => request<Template[]>('/api/templates'),
  createTemplate: (name: string, fields: TemplateFields) => request<Template>('/api/templates', json({ name, fields })),
  updateTemplate: (id: string, name: string, fields: TemplateFields) => request<Template>(`/api/templates/${id}`, put({ name, fields })),
  deleteTemplate: (id: string) => request<unknown>(`/api/templates/${id}`, { method: 'DELETE' }),
  applyTemplate: (id: string, item_ids: string[]) =>
    request<{ items: Item[]; batch_id: string }>(`/api/templates/${id}/apply`, json({ item_ids })),

  // ---------- triage, palette ----------
  triageNext: (o: { view: string; after?: string | null; folder?: string | null; prefetch?: number }) => {
    const p = new URLSearchParams({ view: o.view, prefetch: String(o.prefetch ?? 5) })
    if (o.after) p.set('after', o.after)
    if (o.folder) p.set('folder', o.folder)
    return request<TriageNext>(`/api/triage/next?${p}`)
  },
  palette: (q: string, signal?: AbortSignal) =>
    request<PaletteEntry[]>(`/api/palette?q=${encodeURIComponent(q)}`, signal ? { signal } : {}),
}

/** POST /api/items/{id}/versions with progress (multipart 'file'). */
export function uploadVersion(itemId: string, file: File, onProgress: (pct: number) => void): Promise<{ item: Item; created: boolean }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/items/${itemId}/versions`)
    xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`)
    for (const [k, v] of Object.entries(CLIENT_HEADERS)) xhr.setRequestHeader(k, v)
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText))
      else {
        let detail = `upload failed (${xhr.status})`
        try {
          detail = detailText(JSON.parse(xhr.responseText).detail) ?? detail
        } catch {
          /* not json */
        }
        reject(new ApiError(xhr.status, detail))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'network error'))
    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
}
