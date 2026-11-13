// Present mode (Kist 2.0 F2): the network half. Slides, saved shows, the
// PDF, the phone remote (the show window's side) and the edits a show
// makes (pick / reject / note, through the ordinary item endpoints, so they
// land in the item history and Recent changes can undo them).

import { api, ApiError, CLIENT_HEADERS, getToken, json, request, withBatch } from './api'
import { api3 } from './creativeApi'
import type { PairMode, Resolved, SavedShow, ShowOptions, ShowSource } from './present'

export interface RemoteSession {
  id: string
  name: string
  token: string
  path: string
  expires_at: string
  connected: boolean
}

export interface RemotePoll {
  seq: number
  events: { seq: number; cmd: string; index?: number }[]
  connected: boolean
  expires_at: string
}

export interface PdfOptions {
  layout: 'slides' | 'sheet'
  page: 'a4' | 'a3' | 'letter' | 'tabloid'
  notes: boolean
  captions: boolean
  filenames: boolean
}

function withParams(path: string, params?: string): string {
  if (!params) return path
  const p = new URLSearchParams(params)
  p.delete('limit')
  p.delete('cursor')
  const qs = p.toString()
  return qs ? `${path}?${qs}` : path
}

export const presentApi = {
  slides: (source: ShowSource, opts: { params?: string; compare?: [string, string][]; pairs?: PairMode } = {}) =>
    request<Resolved>(
      withParams('/api/present/slides', source.kind === 'search' ? opts.params : undefined),
      json({ source, compare: opts.compare ?? null, pairs: opts.pairs ?? 'none' }),
      60_000,
    ),
  shows: () => request<SavedShow[]>('/api/shows'),
  showSlides: (id: string) => request<Resolved>(`/api/shows/${id}/slides`, {}, 60_000),
  createShow: (body: { name: string; source: ShowSource; options: ShowOptions; compare: [string, string][]; item_ids?: string[] }) =>
    request<SavedShow>('/api/shows', json(body)),
  patchShow: (id: string, patch: { name?: string; options?: Partial<ShowOptions>; compare?: [string, string][] }) =>
    request<SavedShow>(`/api/shows/${id}`, { ...json(patch), method: 'PATCH' }),
  deleteShow: (id: string) => request<void>(`/api/shows/${id}`, { method: 'DELETE' }),

  /** The PDF as a blob and its file name. */
  pdf: async (
    body: { source: ShowSource; compare?: [string, string][]; pairs?: PairMode; show_id?: string; name?: string },
    pdf: PdfOptions,
    params?: string,
  ): Promise<{ blob: Blob; name: string }> => {
    const res = await fetch(withParams('/api/present/pdf', body.source.kind === 'search' ? params : undefined), {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}`, 'Content-Type': 'application/json', ...CLIENT_HEADERS },
      body: JSON.stringify({ ...body, pdf }),
      signal: AbortSignal.timeout(300_000),
    })
    if (!res.ok) {
      let detail = `error ${res.status}`
      try {
        const data = (await res.json()) as { detail?: string }
        if (typeof data.detail === 'string') detail = data.detail
      } catch {
        /* not json */
      }
      throw new ApiError(res.status, detail)
    }
    const cd = res.headers.get('Content-Disposition') ?? ''
    const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? 'show.pdf'
    return { blob: await res.blob(), name }
  },

  // ---- phone remote (the show window's side) ----
  pair: (name: string, item_ids: string[]) => request<RemoteSession>('/api/present/remote', json({ name, item_ids })),
  poll: (id: string, after: number, signal?: AbortSignal) =>
    request<RemotePoll>(`/api/present/remote/${id}/poll?after=${after}&wait=20`, { signal }, 30_000),
  publish: (id: string, state: Record<string, unknown>) =>
    request<unknown>(`/api/present/remote/${id}/state`, json(state)),
  unpair: (id: string) => request<void>(`/api/present/remote/${id}`, { method: 'DELETE' }),

  // ---- marks during the show ----
  setFlag: (id: string, flag: 'pick' | 'reject' | null, batch?: string) => api.patchItem(id, { flag }, batch),
  /** N: a note on the whole image (an annotation, so it shows on the item
   * and in its history) */
  addNote: (id: string, text: string) =>
    api3.addAnnotation(id, { kind: 'box', x: 0, y: 0, w: 1, h: 1, text }),
}

export { withBatch }
