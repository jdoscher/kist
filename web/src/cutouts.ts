// F7 cutouts: the subject of an image as a new transparent item
// (kist/services/cutouts.py). Requests, the choices the dialog offers, and
// the words it shows; no React here (tests/cutouts.test.ts).

import { json, request, withBatch, type Item, type Job } from './api.ts'

export type CutoutModelState = 'missing' | 'downloading' | 'ready' | 'unknown'

export interface CutoutModel {
  name: string
  label: string
  note: string
  license: string | null
  size_bytes: number | null
  state: CutoutModelState
  downloaded_bytes: number
  error: string | null
}

export interface CutoutStatus {
  enabled: boolean
  available: boolean
  reason: string | null
  default_model: string
  models: CutoutModel[]
  feather_max: number
}

export interface CutoutOptions {
  model: string | null
  feather: number
  shadow: boolean
}

/** What made a cut-out (its media.cutout, kept in meta.json). */
export interface CutoutInfo {
  source_item: string
  model: string
  feather: number
  shadow: boolean
  box: [number, number, number, number]
  size: [number, number]
  ms: number
  made_at: string
}

export interface CutoutJob extends Job {
  result_item_id?: string | null
}

export interface BoardCutoutResult {
  /** an existing cut-out was swapped in at once */
  board: unknown | null
  /** or one is being made; the node switches when it's done */
  job: CutoutJob | null
}

export const cutoutApi = {
  status: () => request<CutoutStatus>('/api/cutouts/status'),
  one: (itemId: string, o: CutoutOptions, batch?: string) =>
    request<CutoutJob>(`/api/items/${itemId}/cutout`, withBatch(json(o), batch)),
  many: (itemIds: string[], o: CutoutOptions, batch?: string) =>
    request<{ jobs: CutoutJob[]; skipped: { item_id: string; reason: string }[] }>('/api/cutouts', withBatch(json({ ...o, item_ids: itemIds }), batch)),
  boardNode: (boardId: string, nodeId: string, o: CutoutOptions, batch?: string) =>
    request<BoardCutoutResult>(`/api/boards/${boardId}/nodes/${encodeURIComponent(nodeId)}/cutout`, withBatch(json(o), batch)),
  list: (itemId: string) => request<Item[]>(`/api/items/${itemId}/cutouts`),
  job: (id: number) => request<CutoutJob>(`/api/jobs/${id}`),
}

export const DEFAULT_OPTIONS: CutoutOptions = { model: null, feather: 0, shadow: false }

export function cutoutInfo(item: Pick<Item, 'media'>): CutoutInfo | null {
  const c = item.media?.cutout
  return c && typeof c === 'object' ? (c as CutoutInfo) : null
}

export function isCutout(item: Pick<Item, 'media'>): boolean {
  return cutoutInfo(item) !== null
}

/** Can this item be cut out? (The server says why not, too.) */
export function canCutOut(item: Pick<Item, 'kind' | 'mime' | 'preview_status' | 'state' | 'media'>): boolean {
  return item.kind === 'image' && item.mime !== 'image/svg+xml' && item.preview_status === 'ready' && item.state === 'active' && !isCutout(item)
}

/** The transparent copy a board draws (the grid's previews are flattened). */
export function cutoutPreviewUrl(item: Pick<Item, 'id' | 'preview_rev'>, size: 1024 | 2048 = 1024): string {
  return `/api/items/${item.id}/cutout-preview?size=${size}&v=${item.preview_rev}`
}

export function fmtMB(bytes: number | null | undefined): string {
  if (!bytes) return '? MB'
  return `${Math.round(bytes / 1e6)} MB`
}

/** The first-use download line for the chosen model, or null when it's there. */
export function downloadNotice(status: CutoutStatus | undefined, model: string | null): string | null {
  if (!status) return null
  const m = status.models.find((x) => x.name === (model || status.default_model))
  if (!m || m.state === 'ready' || m.state === 'unknown') return null
  if (m.state === 'downloading') return `Kist is downloading the ${m.label.toLowerCase()} model (${fmtMB(m.downloaded_bytes)} of ${fmtMB(m.size_bytes)}). Cut-outs start when it's there.`
  return `The first cut-out downloads the ${m.label.toLowerCase()} model to this server, once (${fmtMB(m.size_bytes)}${m.license ? `, ${m.license}` : ''}). It runs here; your images never leave the server.`
}

/** "Fast" / "Fine edges" choices, the default first. */
export function modelChoices(status: CutoutStatus | undefined): CutoutModel[] {
  const list = status?.models ?? []
  return [...list].sort((a, b) => (a.name === status?.default_model ? -1 : b.name === status?.default_model ? 1 : 0))
}

export function optionErrors(o: CutoutOptions, featherMax = 20): string[] {
  const out: string[] = []
  if (!Number.isFinite(o.feather) || o.feather < 0 || o.feather > featherMax) out.push(`Feather is 0 to ${featherMax} px.`)
  return out
}

/** One line about a cut-out job, for the dialog's live region. */
export function jobText(job: Pick<CutoutJob, 'status' | 'last_error'> | undefined): string {
  if (!job || job.status === 'pending') {
    const err = job?.last_error ?? ''
    if (/downloading the cut-out model/.test(err)) {
      const m = /\(([^)]*MB)\)/.exec(err)
      return `Downloading the model${m ? ` (${m[1]})` : ''}; the cut-out starts when it's there…`
    }
    // the server's own words are plain; drop the technical tail in brackets
    if (err) return `${err.replace(/\s*\([A-Za-z]+(Error|Timeout)\)$/, '')}…`
    return 'Waiting for the worker…'
  }
  if (job.status === 'running') return 'Cutting out the subject…'
  if (job.status === 'done') return 'The cut-out is ready.'
  return `The cut-out failed: ${job.last_error ?? 'unknown error'}.`
}

export function isFinished(job: Pick<CutoutJob, 'status'> | undefined): boolean {
  return job?.status === 'done' || job?.status === 'failed'
}

// ---------- who asked for the dialog (a tiny store; CutoutHost shows it) ----------

export interface CutoutRequest {
  items: Item[]
  /** a board's "Cut out" on a placed image */
  board?: { boardId: string; nodeId: string }
}

let current: CutoutRequest | null = null
const listeners = new Set<() => void>()

export function openCutout(req: CutoutRequest): void {
  current = req
  for (const l of listeners) l()
}

export function closeCutout(): void {
  current = null
  for (const l of listeners) l()
}

export function cutoutRequest(): CutoutRequest | null {
  return current
}

export function subscribeCutout(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}
