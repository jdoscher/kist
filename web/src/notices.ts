// The one way the app tells you something happened or failed (no React;
// tests/notices.test.ts). components/Notices.tsx renders the list in a
// polite live region.
//
// - info notices go away on their own; errors stay until dismissed or
//   until the same action succeeds (they share a `key`)
// - a notice may carry one action button (Retry, Undo, Show)
// - a new notice with the same key replaces the old one, so a failing
//   retry doesn't stack copies

export type Tone = 'info' | 'error'

export interface NoticeAction {
  label: string
  run: () => void
}

export interface NoticeInput {
  message: string
  tone?: Tone
  action?: NoticeAction
  /** notices with the same key replace each other */
  key?: string
  /** auto-dismiss after this long; null keeps it until dismissed */
  timeoutMs?: number | null
}

export interface Notice {
  id: number
  message: string
  tone: Tone
  action?: NoticeAction
  key?: string
  timeoutMs: number | null
}

export const INFO_MS = 5000
/** long enough to reach an Undo/Show button */
export const ACTION_MS = 8000
export const MAX_NOTICES = 4

export function toNotice(input: NoticeInput, id: number): Notice {
  const tone = input.tone ?? 'info'
  const timeoutMs =
    input.timeoutMs !== undefined ? input.timeoutMs : tone === 'error' ? null : input.action ? ACTION_MS : INFO_MS
  return { id, message: input.message, tone, action: input.action, key: input.key, timeoutMs }
}

/** Add a notice: replaces one with the same key, keeps the newest few. */
export function addNotice(list: Notice[], notice: Notice, max = MAX_NOTICES): Notice[] {
  const rest = notice.key ? list.filter((n) => n.key !== notice.key) : list
  const next = [...rest, notice]
  return next.length > max ? next.slice(next.length - max) : next
}

/** Remove by id, or every notice with the key. */
export function removeNotice(list: Notice[], idOrKey: number | string): Notice[] {
  const next = list.filter((n) => (typeof idOrKey === 'number' ? n.id !== idOrKey : n.key !== idOrKey))
  return next.length === list.length ? list : next
}
