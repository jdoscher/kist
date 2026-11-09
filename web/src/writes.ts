// What happens when a write fails, is slow, or is sent twice (no React;
// tests/writes.test.ts). From the 2026-11-09 failure-mode review.

import { ApiError } from './api.ts'

/**
 * Writes to one item go out one at a time, in the order they were made, so
 * the server ends on the last click and the screen shows the server's
 * answer to it (two quick star clicks could otherwise land in either order).
 */
const chains = new Map<string, Promise<unknown>>()

export function serialize<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const prev = chains.get(key) ?? Promise.resolve()
  const next = prev.then(fn, fn) // a failed write doesn't block the next one
  const tail = next.catch(() => undefined)
  chains.set(key, tail)
  void tail.then(() => {
    if (chains.get(key) === tail) chains.delete(key)
  })
  return next
}

/** Unsaved text (notes, filename) kept per item until the server has it, so
 * a failed save isn't thrown away when you select another item. */
const drafts = new Map<string, string>()

export const draftKey = (itemId: string, field: string) => `${itemId}:${field}`
export const getDraft = (key: string) => drafts.get(key)
export const setDraft = (key: string, value: string) => void drafts.set(key, value)
export const clearDraft = (key: string) => void drafts.delete(key)
export const hasDrafts = () => drafts.size > 0

/** A save of `sent` finished: drop the draft only if it still holds what
 * was sent. Text typed while the save was in flight stays a draft (and on
 * screen) instead of being replaced by the server's older copy. */
export function clearDraftIf(key: string, sent: string) {
  if (drafts.get(key) === sent) drafts.delete(key)
}

type DiscardListener = (value: string) => void
const discardListeners = new Map<string, Set<DiscardListener>>()

/** Throw the draft away and show `value` instead ("Use theirs"): an open
 * field for the key is told, so it can replace what's on screen. */
export function discardDraft(key: string, value: string) {
  drafts.delete(key)
  discardListeners.get(key)?.forEach((fn) => fn(value))
}

export function onDiscard(key: string, fn: DiscardListener): () => void {
  let set = discardListeners.get(key)
  if (!set) discardListeners.set(key, (set = new Set()))
  set.add(fn)
  return () => {
    set.delete(fn)
    if (set.size === 0) discardListeners.delete(key)
  }
}

/** The later of two ISO timestamps (either may be missing): the
 * modified_at to send as base_modified_at is the newest copy this tab has. */
export function laterStamp(a: string | undefined | null, b: string | undefined | null): string | undefined {
  if (!a) return b ?? undefined
  if (!b) return a
  const ta = Date.parse(a)
  const tb = Date.parse(b)
  if (Number.isNaN(ta) || Number.isNaN(tb)) return a
  if (ta !== tb) return ta > tb ? a : b
  // same millisecond: compare the full fraction (the server keeps µs)
  const frac = (s: string) => (s.match(/\.(\d+)/)?.[1] ?? '').padEnd(9, '0')
  return frac(b) > frac(a) ? b : a
}

/** What a 422 for a deleted folder reads as. */
export const FOLDER_GONE = 'That folder no longer exists (it was deleted, maybe in another tab)'

/** A short, plain reason for a failed request. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return 'No connection to the server'
    if (e.status === 401) return 'Signed out: enter the token again'
    if (e.status === 408) return 'The server took too long'
    if (e.status >= 500) return `Server error (${e.status}${e.requestId ? `, ref ${e.requestId}` : ''})`
    if (e.status === 422 && e.message.startsWith('Unknown folder')) return FOLDER_GONE
    return e.message
  }
  if (e instanceof DOMException && (e.name === 'TimeoutError' || e.name === 'AbortError'))
    return 'The server took too long'
  if (e instanceof TypeError) return 'No connection to the server'
  return String((e as Error)?.message ?? e)
}

export interface EachResult<T> {
  done: T[]
  failed: { id: string; error: string }[]
}

/** Run fn for every id, one at a time, carrying on past failures. */
export async function runEach<T>(ids: string[], fn: (id: string) => Promise<T>): Promise<EachResult<T>> {
  const out: EachResult<T> = { done: [], failed: [] }
  for (const id of ids) {
    try {
      out.done.push(await fn(id))
    } catch (e) {
      out.failed.push({ id, error: describeError(e) })
      // signed out: every remaining request would fail the same way
      if (e instanceof ApiError && e.status === 401) {
        const rest = ids.slice(ids.indexOf(id) + 1)
        out.failed.push(...rest.map((r) => ({ id: r, error: describeError(e) })))
        break
      }
    }
  }
  return out
}

// ---- the write state behind useWrite (useWrite.ts) ----

/** What a component's writes are doing: which keys are in flight, and the
 * latest failure (the notice with Retry shows it too). */
export interface WriteState {
  saving: string[]
  failure: { key: string; message: string } | null
}

export type WriteEvent =
  | { type: 'start'; key: string }
  | { type: 'ok'; key: string }
  | { type: 'fail'; key: string; message: string }
  | { type: 'dismiss' }

export const idleWrites: WriteState = { saving: [], failure: null }

export function writeReducer(s: WriteState, e: WriteEvent): WriteState {
  switch (e.type) {
    case 'start':
      return { ...s, saving: [...s.saving, e.key] }
    case 'ok': {
      // a success clears only its own key's failure
      const i = s.saving.indexOf(e.key)
      const saving = i < 0 ? s.saving : [...s.saving.slice(0, i), ...s.saving.slice(i + 1)]
      return { saving, failure: s.failure?.key === e.key ? null : s.failure }
    }
    case 'fail': {
      const i = s.saving.indexOf(e.key)
      const saving = i < 0 ? s.saving : [...s.saving.slice(0, i), ...s.saving.slice(i + 1)]
      return { saving, failure: { key: e.key, message: e.message } }
    }
    case 'dismiss':
      return s.failure ? { ...s, failure: null } : s
  }
}

/** "Couldn't save the notes: No connection to the server" */
export function failureText(what: string, message: string): string {
  return `Couldn't ${what}: ${message}`
}

/** A 422 because a folder the screen offered no longer exists. */
export function isFolderGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && e.message.startsWith('Unknown folder')
}

/** 401s bring back the token screen, which says what's wrong: no notice. */
export function isSignedOut(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401
}

/** The server banner: the server answering with an error (its database is
 * down: /api/health checks only that) reads differently from no answer. */
export function bannerText(status: number | undefined): string {
  if (status !== undefined && status >= 500) return "Kist is up but its database isn't answering. Retrying…"
  return "Can't reach the Kist server. Retrying…"
}
