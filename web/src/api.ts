// Server-defined states (kist/lifecycle.py); the UI never derives these itself.
type ItemState = 'active' | 'trashed'
export type PreviewStatus = 'pending' | 'ready' | 'failed' | 'none'
export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

export interface Job {
  id: number
  kind: string
  status: JobStatus
  attempts: number
  filename: string | null
  item_id: string | null
  last_error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Item {
  id: string
  filename: string
  ext: string
  mime: string
  kind: 'image' | 'video' | 'pdf' | 'other'
  size_bytes: number
  width: number | null
  height: number | null
  duration_s: number | null
  sha256: string
  phash: number | null
  dominant_colors: { hex: string; pct: number }[] | null
  /** base64 ThumbHash for the loading placeholder (newer servers only) */
  thumbhash?: string | null
  /** can't be trashed or deleted until unprotected (Kist 2.0) */
  protected?: boolean
  folder_ids: string[]
  rating: number
  notes: string
  source_url: string | null
  tags: string[]
  state: ItemState
  preview_status: PreviewStatus
  preview_error: string | null
  preview_rev: number // changes when the preview files do: part of every thumbnail URL
  has_thumb: boolean
  thumb_url: string | null
  created_at: string
  modified_at: string
  deleted_at: string | null
}

export interface ItemPage {
  items: Item[]
  next_cursor: string | null
  total: number | null
}

export interface Folder {
  id: string
  name: string
  parent_id: string | null
  position: number
  /** what belongs in it (up to 5,000 characters; Kist 2.0) */
  description?: string
  item_count: number
}

export interface Tag {
  id: number
  name: string
  count: number
}

export interface Stats {
  total_items: number
  trashed_items: number
  total_size_bytes: number
  by_kind: Record<string, number>
  tag_count: number
  folder_count: number
  failed_jobs: number
  preview_failed: number
  /** originals whose last fixity check failed (Kist 2.0) */
  fixity_failed?: number
}

export interface Query {
  q: string
  tags: string[]
  tags_any: string[]
  untagged: boolean
  view: 'unfiled' | null // the inbox: no folder and no tags (server-defined)
  folder: string | null // folder id, or 'none' for items in no folder
  preview: PreviewStatus | null
  /** 'failed': originals whose fixity check failed */
  fixity: 'failed' | null
  type: string | null
  rating: number
  order: string
  deleted: boolean
  created_after: string | null
  created_before: string | null
}

export const emptyQuery: Query = {
  q: '',
  tags: [],
  tags_any: [],
  untagged: false,
  view: null,
  folder: null,
  preview: null,
  fixity: null,
  type: null,
  rating: 0,
  order: '-created',
  deleted: false,
  created_after: null,
  created_before: null,
}

export interface SmartFolder {
  id: string
  name: string
  query: Partial<Query>
  position: number
}

/** GET /api/me: who this token is. */
export interface Me {
  name: string
  scopes: string[]
  folder_ids: string[]
  expires_at: string | null
}

export interface ApiToken {
  id: string
  name: string
  scopes: string[]
  folder_ids: string[]
  created_at: string
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  active: boolean
}

export interface ItemEvent {
  id: number
  item_id: string
  at: string
  actor: string
  source: string
  batch_id: string
  kind: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

export interface Batch {
  batch_id: string
  actor: string
  source: string
  at: string
  count: number
  kinds: string[]
}

export interface UndoResult {
  undone: number
  skipped: { item_id: string; event_id: number; reason: string }[]
  /** the batch the undo was recorded as (undo it to redo) */
  batch_id: string
}

export type LinkKind = 'derived_from' | 'alternate' | 'related' | 'not_duplicate' | 'version_of'

export interface ItemLite {
  id: string
  filename: string
  kind: string
  state: string
  thumb_url: string | null
  thumbhash: string | null
  width: number | null
  height: number | null
}

export interface ItemLink {
  item: ItemLite
  kind: LinkKind
  direction: 'out' | 'in'
}

export interface ArchiveHealth {
  fixity: {
    checked_30d: number
    total: number
    failed: { id: string; filename: string; failed_at: string | null; expected_sha256: string }[]
  }
  verify: { last_run: string | null; items_checked: number | null; drift: unknown; ok: boolean }
  backup: {
    last_at: string | null
    last_ok_at: string | null
    ok: boolean
    repo_set: boolean
    age_h: number | null
    error: string | null
    snapshot_id: string | null
    db_dump_ok?: boolean | null
    db_dump_error?: string | null
    interval_hours: number
  }
  disk: { free_bytes: number | null }
  trash_retention_days?: number
  checklist: { id: string; label: string; done: boolean }[]
}

export interface AiStatus {
  enabled: boolean
  model: string
  ml_url_set?: boolean
  ml_ok: boolean
  embedded: number
  ocr_done: number
  pending: number
  jobs?: Record<string, Record<string, number>>
  window: string | null
  window_open?: boolean
  concurrency?: number
  helpers: { name: string; last_seen: string | null; jobs_done?: number }[]
}

/** Sent on every API request: the server records edits made here with
 * source "web" in the item history (otherwise "api"). */
export const CLIENT_HEADERS: Readonly<Record<string, string>> = { 'X-Kist-Client': 'web' }

const TOKEN_KEY = 'kist_token'

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setToken(token: string) {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* private mode */
  }
  // cookie lets <img>/<video> tags authenticate without token-bearing URLs
  document.cookie = `kist_token=${encodeURIComponent(token)}; path=/; max-age=31536000; SameSite=Lax`
}

export class ApiError extends Error {
  status: number
  /** the server's X-Request-ID (500s also carry it in the body): shown as
   * "ref …" so a report can be matched to the server log */
  requestId?: string
  /** the parsed error body, e.g. a 409's {detail: {message, item}} */
  data?: unknown
  constructor(status: number, message: string, opts: { requestId?: string; data?: unknown } = {}) {
    super(message)
    this.status = status
    this.requestId = opts.requestId
    this.data = opts.data
  }
}

/** A readable message from a FastAPI error body's `detail`: a string, a
 * 422's list of {msg}, or a {message} object (409). */
export function detailText(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : null)).filter(Boolean)
    if (msgs.length) return msgs.join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message)
  if (detail == null) return null
  try {
    return JSON.stringify(detail)
  } catch {
    return String(detail)
  }
}

/** The current copy of the item a 409 answer carries, if any. */
export function conflictItem(e: unknown): Item | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  const d = (e.data as { detail?: { item?: Item } } | undefined)?.detail
  return d?.item ?? null
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

/** JSON requests give up after this long (URL adds get longer: the server
 * may spend up to 120 s downloading). */
const REQUEST_TIMEOUT_MS = 30_000

/** Tell the app the token stopped working (any request, including uploads). */
function notifyUnauthorized() {
  onUnauthorized?.()
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      signal: init.signal ?? AbortSignal.timeout(timeoutMs),
      headers: {
        Authorization: `Bearer ${getToken()}`,
        ...CLIENT_HEADERS,
        ...(init.headers ?? {}),
      },
    })
  } catch (e) {
    if (e instanceof DOMException && (e.name === 'TimeoutError' || e.name === 'AbortError'))
      throw new ApiError(408, 'The server took too long')
    throw new ApiError(0, 'No connection to the server')
  }
  if (res.status === 401) {
    notifyUnauthorized()
    throw new ApiError(401, 'unauthorized')
  }
  if (!res.ok) {
    let detail = res.statusText || `error ${res.status}`
    let requestId = res.headers.get('X-Request-ID') ?? undefined
    let data: unknown
    try {
      data = await res.json()
      const body = data as { detail?: unknown; request_id?: string }
      detail = detailText(body.detail) ?? detail
      if (body.request_id) requestId = body.request_id
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail, { requestId, data })
  }
  return (await res.json()) as T
}

/** Requests sent with the same X-Kist-Batch id are one batch in the item
 * history, so a bulk action is undone in one step. */
export function newBatchId(now = Date.now(), rand = Math.random): string {
  return `web-${now.toString(36)}-${Math.floor(rand() * 36 ** 6).toString(36).padStart(6, '0')}`
}

function withBatch(init: RequestInit, batch?: string): RequestInit {
  if (!batch) return init
  return { ...init, headers: { ...((init.headers as Record<string, string>) ?? {}), 'X-Kist-Batch': batch } }
}

function json(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

function itemsUrl(query: Query, cursor?: string, limit = 120): string {
  const p = new URLSearchParams()
  if (query.q) p.set('q', query.q)
  if (query.tags.length) p.set('tags', query.tags.join(','))
  if (query.tags_any?.length) p.set('tags_any', query.tags_any.join(','))
  if (query.untagged) p.set('untagged', 'true')
  if (query.view) p.set('view', query.view)
  if (query.folder) p.set('folder', query.folder)
  if (query.preview) p.set('preview_status', query.preview)
  if (query.fixity) p.set('fixity', query.fixity)
  if (query.type) p.set('type', query.type)
  if (query.rating) p.set('rating', String(query.rating))
  if (query.deleted) p.set('state', 'trashed')
  if (query.created_after) p.set('created_after', query.created_after)
  if (query.created_before) p.set('created_before', query.created_before)
  p.set('order', query.order)
  p.set('limit', String(limit))
  if (cursor) p.set('cursor', cursor)
  return `/api/items?${p}`
}

export const api = {
  health: () => request<{ status: string }>('/api/health'),
  version: () => request<{ version: string; commit: string }>('/api/version'),
  listItems: (query: Query, cursor?: string) => request<ItemPage>(itemsUrl(query, cursor)),
  getItem: (id: string) => request<Item>(`/api/items/${id}`),
  /** base_modified_at: the item's modified_at as last seen; the server
   * answers 409 (conflictItem) if someone else changed it since */
  patchItem: (
    id: string,
    patch: Partial<Pick<Item, 'filename' | 'rating' | 'notes' | 'folder_ids' | 'source_url' | 'protected'>> & {
      base_modified_at?: string
    },
    batch?: string,
  ) => request<Item>(`/api/items/${id}`, withBatch({ ...json(patch), method: 'PATCH' }, batch)),
  putFolders: (id: string, folder_ids: string[], batch?: string) =>
    request<Item>(`/api/items/${id}/folders`, withBatch({ ...json({ folder_ids }), method: 'PUT' }, batch)),
  // add/remove against what the server has: never overwrites another writer
  patchTags: (id: string, change: { add?: string[]; remove?: string[] }, batch?: string) =>
    request<Item>(`/api/items/${id}/tags`, withBatch({ ...json(change), method: 'PATCH' }, batch)),
  patchFolders: (id: string, change: { add?: string[]; remove?: string[] }, batch?: string) =>
    request<Item>(`/api/items/${id}/folders`, withBatch({ ...json(change), method: 'PATCH' }, batch)),
  listSmartFolders: () => request<SmartFolder[]>('/api/smart-folders'),
  createSmartFolder: (name: string, query: Partial<Query>) =>
    request<SmartFolder>('/api/smart-folders', json({ name, query })),
  patchSmartFolder: (id: string, patch: { name?: string; position?: number }) =>
    request<SmartFolder>(`/api/smart-folders/${id}`, { ...json(patch), method: 'PATCH' }),
  deleteSmartFolder: (id: string) =>
    request<unknown>(`/api/smart-folders/${id}`, { method: 'DELETE' }),
  trashItem: (id: string, batch?: string) =>
    request<{ status: string; item: Item }>(`/api/items/${id}`, withBatch({ method: 'DELETE' }, batch)),
  hardDeleteItem: (id: string, batch?: string) =>
    request<unknown>(`/api/items/${id}?hard=true`, withBatch({ method: 'DELETE' }, batch)),
  restoreItem: (id: string, batch?: string) =>
    request<Item>(`/api/items/${id}/restore`, withBatch({ method: 'POST' }, batch)),
  addUrl: (url: string, folder_id: string | null) =>
    request<{ item: Item; created: boolean }>(
      '/api/items',
      json({ url, folder_id: folder_id && folder_id !== 'none' ? folder_id : null }),
      150_000,
    ),
  listFolders: () => request<Folder[]>('/api/folders'),
  createFolder: (name: string, parent_id: string | null, description = '') =>
    request<Folder>('/api/folders', json({ name, parent_id, description })),
  patchFolder: (
    id: string,
    patch: { name?: string; parent_id?: string | null; position?: number; description?: string },
  ) =>
    request<Folder>(`/api/folders/${id}`, { ...json(patch), method: 'PATCH' }),
  deleteFolder: (id: string) => request<unknown>(`/api/folders/${id}`, { method: 'DELETE' }),
  listTags: () => request<Tag[]>('/api/tags'),
  stats: () => request<Stats>('/api/stats'),
  retryPreview: (id: string) => request<Item>(`/api/items/${id}/preview`, { method: 'POST' }),
  listJobs: (status?: JobStatus) =>
    request<Job[]>(`/api/jobs${status ? `?status=${status}` : ''}`),
  retryJob: (id: number) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),
  me: () => request<Me>('/api/me'),
  listTokens: () => request<ApiToken[]>('/api/tokens'),
  createToken: (body: { name: string; scopes: string[]; folder_ids?: string[]; expires_at?: string | null }) =>
    request<ApiToken & { token: string }>('/api/tokens', json(body)),
  revokeToken: (id: string) => request<ApiToken>(`/api/tokens/${id}`, { method: 'DELETE' }),
  history: (id: string) => request<ItemEvent[]>(`/api/items/${id}/history`),
  batches: (limit = 30) => request<Batch[]>(`/api/batches?limit=${limit}`),
  undoBatch: (batchId: string) =>
    request<UndoResult>(`/api/batches/${encodeURIComponent(batchId)}/undo`, { method: 'POST' }),
  revertEvent: (eventId: number) => request<UndoResult>(`/api/events/${eventId}/revert`, { method: 'POST' }),
  links: (id: string) => request<ItemLink[]>(`/api/items/${id}/links`),
  addLink: (id: string, dst_id: string, kind: LinkKind) =>
    request<ItemLink[]>(`/api/items/${id}/links`, json({ dst_id, kind })),
  removeLink: (src: string, dst: string, kind: LinkKind) =>
    request<{ removed: number }>(`/api/items/${src}/links/${dst}?kind=${kind}`, { method: 'DELETE' }),
  archiveHealth: () => request<ArchiveHealth>('/api/archive-health'),
  setChecklist: (id: string, done: boolean) =>
    request<ArchiveHealth>('/api/archive-health/checklist', json({ id, done })),
  aiStatus: () => request<AiStatus>('/api/ai/status'),
  /** a few items by filename, for the link picker */
  searchItems: (q: string, limit = 12) =>
    request<ItemPage>(itemsUrl({ ...emptyQuery, q }, undefined, limit)),
  dismissJob: (id: number) =>
    request<{ status: string; file_left_at: string | null }>(`/api/jobs/${id}`, {
      method: 'DELETE',
    }),
}

export function uploadFile(
  file: File,
  folderId: string | null,
  onProgress: (pct: number) => void,
  sourceUrl?: string,
): Promise<{ item: Item; created: boolean }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/items')
    xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`)
    for (const [k, v] of Object.entries(CLIENT_HEADERS)) xhr.setRequestHeader(k, v)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText))
      } else {
        let detail = `upload failed (${xhr.status})`
        let requestId = xhr.getResponseHeader('X-Request-ID') ?? undefined
        let data: unknown
        try {
          data = JSON.parse(xhr.responseText)
          const body = data as { detail?: unknown; request_id?: string }
          detail = detailText(body.detail) ?? detail
          if (body.request_id) requestId = body.request_id
        } catch {
          /* ignore */
        }
        if (xhr.status === 401) notifyUnauthorized()
        reject(new ApiError(xhr.status, xhr.status === 401 ? 'unauthorized' : detail, { requestId, data }))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'network error'))
    const form = new FormData()
    form.append('file', file, file.name)
    if (folderId && folderId !== 'none') form.append('folder_id', folderId)
    if (sourceUrl) form.append('source_url', sourceUrl)
    xhr.send(form)
  })
}

export function thumbUrl(item: Item, size: 256 | 512 | 1024 = 256): string | null {
  if (!item.has_thumb) return null
  // versioned, so the server lets the browser keep it for good
  return `/api/items/${item.id}/thumb?size=${size}&v=${item.preview_rev}`
}

export function fileUrl(item: Item): string {
  return `/api/items/${item.id}/file`
}
