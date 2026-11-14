import { imageFor } from './imageSearch.ts'
import { addM45Params, type M45Filters } from './m45/filters.ts'
import { addProjectParams, type ProjectFilters } from './projectFilters.ts'
import { bucketParams, scrubSort, type Buckets } from './scrub.ts'
import { reportNetwork } from './shell.ts'

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

/** What the server calls an item's type (kist/filetypes.py KINDS; the
 * Milestone 5 kinds are audio, model, font, design and bundle). */
export const ITEM_KINDS = ['image', 'video', 'pdf', 'audio', 'model', 'font', 'design', 'bundle', 'other'] as const
export type ItemKind = (typeof ITEM_KINDS)[number]

export function isItemKind(v: unknown): v is ItemKind {
  return (ITEM_KINDS as readonly unknown[]).includes(v)
}

export interface Item {
  id: string
  filename: string
  ext: string
  mime: string
  kind: ItemKind
  size_bytes: number
  width: number | null
  height: number | null
  duration_s: number | null
  sha256: string
  phash: number | null
  dominant_colors: { hex: string; pct: number }[] | null
  /** base64 ThumbHash for the loading placeholder (newer servers only) */
  thumbhash?: string | null
  /** F6: custom field values, {field id: value} (projects.ts) */
  custom_fields?: Record<string, string | number>
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
  /** Milestone 2: a sentence describing the image (generated or edited) */
  caption?: string | null
  /** tags the vocabulary implies (boots -> shoes): virtual, never stored */
  implied_tags?: string[]
  /** why it's in a ranked search (search.ts) */
  match?: Match | null
  /** the item's stack and, in a collapsed list, how many members it has */
  stack_id?: string | null
  stack_count?: number | null
  /** culling (Milestone 3): pick | reject, and a colour label */
  flag?: 'pick' | 'reject' | null
  label?: 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple' | null
  // Milestone 4 (typed here; its screens come later): where it came from
  // (method, page_url, page_title, author, license, ai_generated ...)
  provenance?: Record<string, unknown>
  /** the link-rot watch: ok | gone | moved | error, or null (never checked) */
  source_status?: 'ok' | 'gone' | 'moved' | 'error' | null
  source_checked_at?: string | null
  source_wayback_url?: string | null
  // Milestone 5: what Kist read from the file (video, audio, exif, svg,
  // model, font, bundle, design, raw blocks; scrub, playable, cover_time)
  media?: Record<string, unknown> | null
  taken_at?: string | null
  place?: string | null
}

/**
 * A list item as the grid gets it (GET /api/items?fields=grid; Kist 2.0
 * performance work P4, kist/schemas.py GridItemOut): what tiles, selection,
 * bulk actions, keyboard navigation, stacks and the scrubber read. The rest
 * of an item (notes, caption, source, provenance, media blocks, custom
 * fields ...) comes from GET /api/items/{id}: useItem() in data.ts fetches it
 * for the info panel, the phone sheet and the full-screen preview. A full
 * Item is a GridItem too (edits patch full items into the lists).
 *
 * Narrowed on the way: `dominant_colors` holds the first colour only,
 * `provenance` only the AI-made keys (when it is AI-made) and `media` only
 * the `scrub` and `cutout` blocks.
 */
export type GridItem = Pick<
  Item,
  | 'id'
  | 'filename'
  | 'ext'
  | 'mime'
  | 'kind'
  | 'size_bytes'
  | 'width'
  | 'height'
  | 'duration_s'
  | 'dominant_colors'
  | 'thumbhash'
  | 'folder_ids'
  | 'tags'
  | 'rating'
  | 'protected'
  | 'flag'
  | 'label'
  | 'state'
  | 'preview_status'
  | 'preview_rev'
  | 'has_thumb'
  | 'created_at'
  | 'modified_at'
  | 'taken_at'
  | 'match'
  | 'stack_id'
  | 'stack_count'
  | 'provenance'
  | 'media'
>

/** Why an item is in a ranked (or keyword) search. The snippet marks the
 * matched words <<like this>>. */
export interface Match {
  kind: 'keyword' | 'meaning' | 'text' | 'color' | 'similar' | 'hybrid'
  score: number
  snippet?: string | null
}

export interface ItemPage<T extends GridItem = Item> {
  items: T[]
  next_cursor: string | null
  total: number | null
  /** the client fell back to words only (the AI service didn't answer) */
  degraded?: 'meaning'
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
  /** items whose source page is gone (the link-rot watch, Milestone 4) */
  source_gone?: number
  /** items in no folder and with no tags (the Unfiled view; Milestone 6
   * badge). Older servers leave it out: the badge then asks the view. */
  unfiled?: number
}

export interface Query extends M45Filters, ProjectFilters {
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
  // Milestone 2 search (search.ts): ranked modes and filters
  /** search by meaning (a description) */
  semantic: string
  /** more like this item */
  similar: string | null
  /** a hex colour, and how close (0-100) */
  color: string | null
  color_tol: number
  /** only words inside images and PDFs */
  text: string
  /** a person (faces) */
  person: string | null
  /** search by an image that isn't in the library (imageSearch.ts; this tab only) */
  image: string | null
  /** culling filters (Milestone 3): pick | reject | none, a colour | none */
  flag?: string | null
  label?: string | null
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
  semantic: '',
  similar: null,
  color: null,
  color_tol: 30,
  text: '',
  person: null,
  image: null,
  flag: null,
  label: null,
  // F6 (projectFilters.ts): set to null so a fresh view clears them
  project: null,
  custom_fields: null,
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
  skipped: { item_id: string; event_id: number; reason: string; target?: 'item' | 'board' | 'vocabulary' }[]
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
  /** Milestone 2 */
  captioned?: number
  captions?: {
    provider: string | null
    model: string | null
    auto: boolean
    runs_on: 'server' | 'helper' | null
    leaves_house: boolean
    warning: string | null
  }
  faces?: boolean
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

export async function request<T>(
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
    reportNetwork(false) // no answer at all: the Offline banner (shell.ts)
    throw new ApiError(0, 'No connection to the server')
  }
  reportNetwork(true)
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
  // 204 No Content (e.g. removing a token entry) has no body to parse
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Requests sent with the same X-Kist-Batch id are one batch in the item
 * history, so a bulk action is undone in one step. */
export function newBatchId(now = Date.now(), rand = Math.random): string {
  return `web-${now.toString(36)}-${Math.floor(rand() * 36 ** 6).toString(36).padStart(6, '0')}`
}

export function withBatch(init: RequestInit, batch?: string): RequestInit {
  if (!batch) return init
  return { ...init, headers: { ...((init.headers as Record<string, string>) ?? {}), 'X-Kist-Batch': batch } }
}

export function json(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

/** The filters as GET /api/items parameters (also POST /api/search/image's). */
export function itemsParams(query: Query, cursor?: string, limit = 120): URLSearchParams {
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
  if (query.semantic) p.set('semantic', query.semantic)
  if (query.similar) p.set('similar', query.similar)
  if (query.color) {
    p.set('color', query.color)
    p.set('color_tol', String(query.color_tol))
  }
  if (query.text) p.set('text', query.text)
  if (query.person) p.set('person', query.person)
  // a stack shows once (its cover, or in a ranked search the member that
  // matched); the trash lists every item
  if (!query.deleted) p.set('stacks', 'collapse')
  if (query.flag) p.set('flag', query.flag)
  if (query.label) p.set('label', query.label)
  // Milestones 4 and 5: provenance and media filters (m45/filters.ts)
  addM45Params(query, p)
  addProjectParams(query, p) // F6: project=, field.<key>=
  p.set('order', query.order)
  p.set('limit', String(limit))
  if (cursor) p.set('cursor', cursor)
  return p
}

function itemsUrl(query: Query, cursor?: string, limit = 120): string {
  return `/api/items?${itemsParams(query, cursor, limit)}`
}

/** The grid's list pages: the same page with slim items (P4). */
function gridUrl(query: Query, cursor?: string, limit = 120): string {
  const p = itemsParams(query, cursor, limit)
  p.set('fields', 'grid')
  return `/api/items?${p}`
}

export const api = {
  health: () => request<{ status: string }>('/api/health'),
  version: () => request<{ version: string; commit: string }>('/api/version'),
  /** The grid's list: slim items (GridItem, fields=grid). */
  listItems: async (query: Query, cursor?: string): Promise<ItemPage<GridItem>> => {
    if (query.image) return searchByImage(query, cursor)
    try {
      return await request<ItemPage<GridItem>>(gridUrl(query, cursor))
    } catch (e) {
      // words + meaning while the AI service is down: the words alone
      if (e instanceof ApiError && e.status === 503 && query.semantic && query.q) {
        const page = await request<ItemPage<GridItem>>(gridUrl({ ...query, semantic: '' }, cursor))
        return { ...page, degraded: 'meaning' }
      }
      throw e
    }
  },
  /** The list's months for the date scrubber (GET /api/items/buckets, Kist
   * 2.0 M6): same filters as listItems, with its date sort. null when the
   * list isn't sorted by a date. */
  itemBuckets: (query: Query): Promise<Buckets> | null => {
    const s = scrubSort(query)
    if (!s || query.image) return null
    return request<Buckets>(`/api/items/buckets?${bucketParams(itemsParams(query), s)}`)
  },
  /** One page of the list from a cursor (a bucket's, for a scrubber jump),
   * with its own page size (the server allows up to 500). */
  listItemsFrom: (query: Query, cursor: string, limit = 120): Promise<ItemPage<GridItem>> =>
    request<ItemPage<GridItem>>(gridUrl(query, cursor || undefined, Math.min(500, Math.max(1, limit)))),
  getItem: (id: string) => request<Item>(`/api/items/${id}`),
  /** base_modified_at: the item's modified_at as last seen; the server
   * answers 409 (conflictItem) if someone else changed it since */
  patchItem: (
    id: string,
    patch: Partial<Pick<Item, 'filename' | 'rating' | 'notes' | 'folder_ids' | 'source_url' | 'protected' | 'caption' | 'flag' | 'label'>> & {
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
  /** remove a revoked or expired token from the list (409 while it still works) */
  removeTokenEntry: (id: string) => request<void>(`/api/tokens/${id}/entry`, { method: 'DELETE' }),
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

/** POST /api/search/image with the image kept for this tab (imageSearch.ts). */
async function searchByImage(query: Query, cursor?: string): Promise<ItemPage<GridItem>> {
  const blob = query.image ? imageFor(query.image) : undefined
  if (!blob) throw new ApiError(410, 'The image you searched with isn’t kept after a reload: drop or paste it again')
  const form = new FormData()
  form.append('file', blob, 'search-image')
  const p = itemsParams({ ...query, image: null }, cursor)
  p.set('fields', 'grid')
  return request<ItemPage<GridItem>>(`/api/search/image?${p}`, { method: 'POST', body: form }, 60_000)
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

export function thumbUrl(item: Pick<Item, 'id' | 'has_thumb' | 'preview_rev'>, size: 256 | 512 | 1024 = 256): string | null {
  if (!item.has_thumb) return null
  // versioned, so the server lets the browser keep it for good
  return `/api/items/${item.id}/thumb?size=${size}&v=${item.preview_rev}`
}

export function fileUrl(item: Item): string {
  return `/api/items/${item.id}/file`
}
