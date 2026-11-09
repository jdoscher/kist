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

/** A short, plain reason for a failed request. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return 'No connection to the server'
    if (e.status === 401) return 'Signed out: enter the token again'
    if (e.status === 408) return 'The server took too long'
    if (e.status >= 500) return `Server error (${e.status})`
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
