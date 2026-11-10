// Milestones 4 and 5 API calls (README "REST API"). Kept apart from api.ts
// so the first page doesn't carry them.

import { ApiError, CLIENT_HEADERS, getToken, itemsParams, json, request, type Item, type Job, type Query } from '../api'
import type { Bucket } from './timeline'
import type { Share, SharePayload } from './share'
import type { ScrubInfo, ClipFormat } from './media'

export interface ExportRecord {
  id: string
  status: 'pending' | 'running' | 'done' | 'failed'
  format: string
  target: string
  target_id: string | null
  item_ids: string[] | null
  include: Record<string, boolean>
  created_by: string
  created_at: string
  finished_at: string | null
  error: string | null
  item_count: number | null
  size_bytes: number | null
  download_url?: string
  progress?: { done?: number; total?: number } | null
}

export interface ExportPreset {
  name: string
  max_side: number | null
  format: 'jpg' | 'webp' | 'png' | 'avif'
  quality: number
  strip_metadata: boolean
  watermark?: { text: string; position: string; opacity: number } | null
  contact_sheet?: { cols: number; caption: string } | null
}

export interface ImportReport {
  found?: number
  imported?: number
  duplicates?: number
  skipped?: number
  to_download?: number
  errors_count?: number
  errors?: { key: string; error: string }[]
  folder_path?: string
  folder_id?: string
  batch_id?: string | null
  finished_at?: string
  [k: string]: unknown
}

export interface ImportRun {
  id: number
  status: 'pending' | 'running' | 'done' | 'failed'
  kind: string
  source: string
  dry_run: boolean
  report: ImportReport | null
  last_error: string | null
  created_at: string
  finished_at: string | null
}

export interface MapCluster {
  lat: number
  lon: number
  count: number
  item_id: string
  bbox: [number, number, number, number]
}

export interface MapPage {
  zoom: number
  cell_deg: number
  total: number
  clusters: MapCluster[]
  truncated: boolean
}

export interface FontInfo {
  family: string | null
  style: string | null
  full_name: string | null
  version: string | null
  designer: string | null
  license: string | null
  weight: number | null
  glyph_count: number
  codepoint_count: number
  axes: { tag: string; name?: string; min: number; default: number; max: number }[]
  scripts: string[]
  variable: boolean
}

export interface BundleEntry {
  path: string
  size: number
  compressed: number
  kind: string
  safe: boolean
}

/** Filters for /api/timeline, /api/on-this-day and /api/map: the
 * library's, without paging, ordering or stack collapsing. */
export function filterParams(q: Query): URLSearchParams {
  const p = itemsParams(q)
  for (const k of ['order', 'limit', 'cursor', 'stacks', 'semantic', 'similar', 'color', 'color_tol']) p.delete(k)
  return p
}

async function blobRequest(path: string, init: RequestInit): Promise<{ blob: Blob; name: string }> {
  const res = await fetch(path, {
    ...init,
    headers: { Authorization: `Bearer ${getToken()}`, ...CLIENT_HEADERS, ...(init.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail)
  }
  const cd = res.headers.get('Content-Disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? 'kist-export'
  return { blob: await res.blob(), name }
}

export const api45 = {
  // ---------- shares ----------
  listShares: () => request<Share[]>('/api/shares'),
  createShare: (body: SharePayload) => request<Share>('/api/shares', json(body)),
  revokeShare: (id: string) => request<Share>(`/api/shares/${id}/revoke`, { method: 'POST' }),
  deleteShare: (id: string) => request<{ status: string }>(`/api/shares/${id}`, { method: 'DELETE' }),
  // ---------- exports ----------
  createExport: (body: { target: string; target_id?: string; item_ids?: string[]; include: Record<string, boolean>; format: 'bagit-zip' }) =>
    request<ExportRecord>('/api/exports', json(body)),
  getExport: (id: string) => request<ExportRecord>(`/api/exports/${id}`),
  listExports: () => request<ExportRecord[]>('/api/exports'),
  deleteExport: (id: string) => request<{ status: string }>(`/api/exports/${id}`, { method: 'DELETE' }),
  presets: () => request<ExportPreset[]>('/api/export-presets'),
  savePreset: (p: ExportPreset) =>
    request<ExportPreset>(`/api/export-presets/${encodeURIComponent(p.name)}`, { ...json(p), method: 'PUT' }),
  deletePreset: (name: string) =>
    request<{ status: string }>(`/api/export-presets/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  exportImages: (item_ids: string[], preset: string | Omit<ExportPreset, 'name'>) =>
    blobRequest('/api/items/export-images', json({ item_ids, preset })),
  // ---------- imports ----------
  createImport: (body: { kind: string; source: string; dry_run: boolean; folder?: string | null }) =>
    request<ImportRun>('/api/imports', json(body)),
  /** an export file from this computer, kept on the server for POST /api/imports */
  uploadImport: (file: File, onProgress: (pct: number) => void) =>
    new Promise<{ source: string; size_bytes: number; filename: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/imports/upload')
      xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`)
      for (const [k, v] of Object.entries(CLIENT_HEADERS)) xhr.setRequestHeader(k, v)
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100))
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText))
        else {
          let detail = `upload failed (${xhr.status})`
          try {
            const d = JSON.parse(xhr.responseText).detail
            if (typeof d === 'string') detail = d
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
    }),
  getImport: (id: number) => request<ImportRun>(`/api/imports/${id}`),
  shortcut: () =>
    request<{ file_available: boolean; why: string; endpoint: string; token: string; steps: string[] }>('/api/shortcut'),
  // ---------- provenance, snapshots ----------
  patchProvenance: (id: string, provenance: Record<string, unknown>, base_modified_at?: string) =>
    request<Item>(`/api/items/${id}`, { ...json({ provenance, base_modified_at }), method: 'PATCH' }),
  snapshotText: async (id: string): Promise<string> => {
    const res = await fetch(`/api/items/${id}/snapshot/text`, {
      headers: { Authorization: `Bearer ${getToken()}`, ...CLIENT_HEADERS },
    })
    if (!res.ok) throw new ApiError(res.status, res.status === 404 ? 'No saved page for this item' : res.statusText)
    const type = res.headers.get('Content-Type') ?? ''
    if (type.includes('json')) {
      const body = await res.json()
      return typeof body === 'string' ? body : String(body.text ?? '')
    }
    return res.text()
  },
  hasSnapshot: async (id: string): Promise<boolean> => {
    const res = await fetch(`/api/items/${id}/snapshot/text`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${getToken()}`, ...CLIENT_HEADERS, Range: 'bytes=0-0' },
    })
    return res.ok
  },
  saveSnapshot: (id: string) => request<unknown>(`/api/items/${id}/snapshot`, { method: 'POST' }, 90_000),
  // ---------- video, audio ----------
  scrubInfo: (id: string) => request<ScrubInfo>(`/api/items/${id}/scrub.json`),
  setCover: (id: string, t: number | null) => request<Item>(`/api/items/${id}/cover`, json({ t })),
  saveFrame: (id: string, t: number) => request<{ item: Item; created: boolean }>(`/api/items/${id}/frames`, json({ t }), 60_000),
  clip: (id: string, body: { start: number; end: number; format: ClipFormat; max_side?: number | null }) =>
    request<Job & { result_item_id?: string | null }>(`/api/items/${id}/clip`, json(body)),
  job: (id: number) => request<Job & { result_item_id?: string | null; progress?: unknown }>(`/api/jobs/${id}`),
  /** 200: playable now; 202: a proxy is being made */
  streamState: async (id: string): Promise<'ready' | 'preparing' | 'none'> => {
    const res = await fetch(`/api/items/${id}/stream`, {
      headers: { Authorization: `Bearer ${getToken()}`, ...CLIENT_HEADERS, Range: 'bytes=0-1' },
    })
    if (res.status === 202) return 'preparing'
    if (res.ok) return 'ready'
    return 'none'
  },
  waveform: (id: string) => request<{ data: number[]; channels: number; length: number; bits: number }>(`/api/items/${id}/waveform`),
  svgText: async (id: string): Promise<string> => {
    const res = await fetch(`/api/items/${id}/svg`, { headers: { Authorization: `Bearer ${getToken()}`, ...CLIENT_HEADERS } })
    if (!res.ok) throw new ApiError(res.status, res.statusText)
    return res.text()
  },
  poster: async (id: string, blob: Blob): Promise<Item> => {
    const form = new FormData()
    form.append('file', blob, 'poster.png')
    return request<Item>(`/api/items/${id}/poster`, { method: 'POST', body: form }, 60_000)
  },
  fontInfo: (id: string) => request<FontInfo>(`/api/items/${id}/font-info`),
  glyphs: (id: string, page: number) =>
    request<{ page: number; pages: number; total: number; glyphs: { cp: number; hex: string; char: string; name: string | null }[] }>(
      `/api/items/${id}/glyphs?page=${page}`,
    ),
  bundle: (id: string) =>
    request<{ entries: BundleEntry[]; count: number; total_size: number; cover: string | null; model: string | null; docs: string[] }>(
      `/api/items/${id}/bundle`,
    ),
  // ---------- timeline, map ----------
  timeline: (group: string, q: Query) => {
    const p = filterParams(q)
    p.set('group', group)
    return request<Bucket[]>(`/api/timeline?${p}`)
  },
  onThisDay: (day: string, q?: Query) => {
    const p = q ? filterParams(q) : new URLSearchParams()
    p.set('day', day)
    p.set('per_year', '8')
    return request<{ date: string; years: { year: number; years_ago: number; items: Item[] }[] }>(`/api/on-this-day?${p}`)
  },
  map: (q: Query, bbox: string | null, zoom: number) => {
    const p = filterParams({ ...q, bbox: null })
    if (bbox) p.set('bbox', bbox)
    p.set('zoom', String(Math.max(0, Math.min(22, Math.round(zoom)))))
    return request<MapPage>(`/api/map?${p}`)
  },
  mapConfig: () => request<{ tiles_url: string | null; attribution: string | null; place_names: boolean }>('/api/map/config'),
}

/** Save a blob as a download. */
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export function streamUrl(item: Pick<Item, 'id'>): string {
  return `/api/items/${item.id}/stream`
}

export function bundleFileUrl(id: string, path: string): string {
  return `/api/items/${id}/bundle/${path.split('/').map(encodeURIComponent).join('/')}`
}
