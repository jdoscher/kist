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
  folder_ids: string[]
  rating: number
  notes: string
  source_url: string | null
  tags: string[]
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
}

export interface Query {
  q: string
  tags: string[]
  tags_any: string[]
  untagged: boolean
  folder: string | null // folder id, or 'none' for unfiled
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
  folder: null,
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
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      ...(init.headers ?? {}),
    },
  })
  if (res.status === 401) {
    onUnauthorized?.()
    throw new ApiError(401, 'unauthorized')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      if (data.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

function json(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export function itemsUrl(query: Query, cursor?: string, limit = 120): string {
  const p = new URLSearchParams()
  if (query.q) p.set('q', query.q)
  if (query.tags.length) p.set('tags', query.tags.join(','))
  if (query.tags_any?.length) p.set('tags_any', query.tags_any.join(','))
  if (query.untagged) p.set('untagged', 'true')
  if (query.folder) p.set('folder', query.folder)
  if (query.type) p.set('type', query.type)
  if (query.rating) p.set('rating', String(query.rating))
  if (query.deleted) p.set('deleted', 'true')
  if (query.created_after) p.set('created_after', query.created_after)
  if (query.created_before) p.set('created_before', query.created_before)
  p.set('order', query.order)
  p.set('limit', String(limit))
  if (cursor) p.set('cursor', cursor)
  return `/api/items?${p}`
}

export const api = {
  health: () => request<{ status: string }>('/api/health'),
  listItems: (query: Query, cursor?: string) => request<ItemPage>(itemsUrl(query, cursor)),
  getItem: (id: string) => request<Item>(`/api/items/${id}`),
  patchItem: (
    id: string,
    patch: Partial<Pick<Item, 'filename' | 'rating' | 'notes' | 'folder_ids' | 'source_url'>>,
  ) => request<Item>(`/api/items/${id}`, { ...json(patch), method: 'PATCH' }),
  putTags: (id: string, tags: string[]) =>
    request<Item>(`/api/items/${id}/tags`, { ...json({ tags }), method: 'PUT' }),
  putFolders: (id: string, folder_ids: string[]) =>
    request<Item>(`/api/items/${id}/folders`, { ...json({ folder_ids }), method: 'PUT' }),
  listSmartFolders: () => request<SmartFolder[]>('/api/smart-folders'),
  createSmartFolder: (name: string, query: Partial<Query>) =>
    request<SmartFolder>('/api/smart-folders', json({ name, query })),
  patchSmartFolder: (id: string, patch: { name?: string; position?: number }) =>
    request<SmartFolder>(`/api/smart-folders/${id}`, { ...json(patch), method: 'PATCH' }),
  deleteSmartFolder: (id: string) =>
    request<unknown>(`/api/smart-folders/${id}`, { method: 'DELETE' }),
  trashItem: (id: string) => request<unknown>(`/api/items/${id}`, { method: 'DELETE' }),
  hardDeleteItem: (id: string) => request<unknown>(`/api/items/${id}?hard=true`, { method: 'DELETE' }),
  restoreItem: (id: string) => request<Item>(`/api/items/${id}/restore`, { method: 'POST' }),
  addUrl: (url: string, folder_id: string | null) =>
    request<{ item: Item; created: boolean }>(
      '/api/items',
      json({ url, folder_id: folder_id && folder_id !== 'none' ? folder_id : null }),
    ),
  listFolders: () => request<Folder[]>('/api/folders'),
  createFolder: (name: string, parent_id: string | null) =>
    request<Folder>('/api/folders', json({ name, parent_id })),
  patchFolder: (id: string, patch: { name?: string; parent_id?: string | null; position?: number }) =>
    request<Folder>(`/api/folders/${id}`, { ...json(patch), method: 'PATCH' }),
  deleteFolder: (id: string) => request<unknown>(`/api/folders/${id}`, { method: 'DELETE' }),
  listTags: () => request<Tag[]>('/api/tags'),
  stats: () => request<Stats>('/api/stats'),
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
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText))
      } else {
        let detail = `upload failed (${xhr.status})`
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail
        } catch {
          /* ignore */
        }
        reject(new ApiError(xhr.status, detail))
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
  return `/api/items/${item.id}/thumb?size=${size}`
}

export function fileUrl(item: Item): string {
  return `/api/items/${item.id}/file`
}
