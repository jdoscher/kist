// The upload queue as an explicit state machine (no React; tested in
// tests/uploads.test.ts).
//
//   queued -> uploading -> done | duplicate | inTrash | error
//   error -> queued (retry)          inTrash -> done (restored)
//   any finished entry -> removed (dismiss / expire)
//
// The destination folder is captured when a file is added, so switching
// folders while a batch uploads doesn't move the rest of it.

type UploadStatus = 'queued' | 'uploading' | 'done' | 'duplicate' | 'inTrash' | 'error'

export interface UploadEntry<J = unknown> {
  key: number
  job: J
  name: string
  isUrl: boolean
  folderId: string | null
  status: UploadStatus
  pct: number
  error?: string
  itemId?: string
  finishedAt?: number
}

export type UploadEvent<J = unknown> =
  | {
      type: 'add'
      entries: { key: number; job: J; name: string; isUrl: boolean; folderId: string | null }[]
    }
  | { type: 'start'; key: number }
  | { type: 'progress'; key: number; pct: number }
  | { type: 'fallback'; key: number } // a URL Kist couldn't fetch: uploading the copied image instead
  | { type: 'finish'; key: number; created: boolean; itemId: string; itemState: string; at: number }
  | { type: 'fail'; key: number; error: string }
  | { type: 'retry'; key: number }
  | { type: 'restored'; key: number; at: number }
  | { type: 'dismiss'; key: number }
  | { type: 'expire'; now: number; afterMs: number }

const FADES: UploadStatus[] = ['done', 'duplicate']

export function uploadsReducer<J>(state: UploadEntry<J>[], ev: UploadEvent<J>): UploadEntry<J>[] {
  const patch = (key: number, p: Partial<UploadEntry<J>>, from: UploadStatus[]) =>
    state.map((e) => (e.key === key && from.includes(e.status) ? { ...e, ...p } : e))
  switch (ev.type) {
    case 'add':
      return [...state, ...ev.entries.map((e) => ({ ...e, status: 'queued' as const, pct: 0 }))]
    case 'start':
      return patch(ev.key, { status: 'uploading', pct: 0, error: undefined }, ['queued'])
    case 'progress':
      return patch(ev.key, { pct: ev.pct }, ['uploading'])
    case 'fallback':
      return patch(ev.key, { isUrl: false }, ['uploading'])
    case 'finish': {
      const status: UploadStatus = ev.created
        ? 'done'
        : ev.itemState === 'trashed'
          ? 'inTrash'
          : 'duplicate'
      return patch(ev.key, { status, pct: 100, itemId: ev.itemId, finishedAt: ev.at }, ['uploading'])
    }
    case 'fail':
      return patch(ev.key, { status: 'error', error: ev.error }, ['uploading'])
    case 'retry':
      return patch(ev.key, { status: 'queued', pct: 0, error: undefined }, ['error'])
    case 'restored':
      return patch(ev.key, { status: 'done', finishedAt: ev.at }, ['inTrash'])
    case 'dismiss':
      return state.filter((e) => e.key !== ev.key || e.status === 'uploading' || e.status === 'queued')
    case 'expire':
      // finished uploads fade out; errors and "in Trash" wait for the user
      return state.filter(
        (e) =>
          !(FADES.includes(e.status) && e.finishedAt !== undefined && ev.now - e.finishedAt >= ev.afterMs),
      )
  }
}

/** The next entry to send, oldest first (one upload at a time). */
export function nextQueued<J>(state: UploadEntry<J>[]): UploadEntry<J> | undefined {
  if (state.some((e) => e.status === 'uploading')) return undefined
  return state.find((e) => e.status === 'queued')
}
