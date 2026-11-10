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

/** The toasts to show for a big batch: every entry that needs an action
 * (error, in Trash) and the one uploading, then the newest finished and the
 * next queued ones, up to `max`; the rest are counted (a 60-file drop used
 * to stack 60 toasts past the top of the window, over the toolbar). Keeps
 * the queue's order. */
export function visibleUploads<J>(
  state: UploadEntry<J>[],
  max = 6,
): { shown: UploadEntry<J>[]; hiddenQueued: number; hiddenDone: number } {
  const keep = new Set<number>()
  for (const e of state) if (e.status === 'error' || e.status === 'inTrash' || e.status === 'uploading') keep.add(e.key)
  const finished = state.filter((e) => (e.status === 'done' || e.status === 'duplicate') && !keep.has(e.key))
  const queued = state.filter((e) => e.status === 'queued')
  // newest finished first, then the next in the queue, alternating so both show
  const fin = [...finished].reverse()
  let i = 0
  let j = 0
  while (keep.size < max && (i < fin.length || j < queued.length)) {
    if (i < fin.length && (i <= j || j >= queued.length)) keep.add(fin[i++].key)
    else keep.add(queued[j++].key)
  }
  const shown = state.filter((e) => keep.has(e.key))
  return {
    shown,
    hiddenQueued: queued.filter((e) => !keep.has(e.key)).length,
    hiddenDone: finished.filter((e) => !keep.has(e.key)).length,
  }
}
