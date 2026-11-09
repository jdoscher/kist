// The one write hook (state rules: writeReducer in writes.ts).
//
//   const w = useWrite()
//   w.run('rename', () => api.patchFolder(...), { what: 'rename the folder', onDone })
//
// Each run reports busy state per key; a failure shows as an error notice
// with Retry (and stays in w.failure for inline hints), and the notice goes
// away when the same key next succeeds. useItemWrite adds the item rules:
// writes to one item go out in order (serialize) and the server's answer
// is patched into every loaded list (useApplyItems), never a refetch.

import { useCallback, useEffect, useId, useReducer, useRef, useState } from 'react'
import type { Item } from './api'
import { useNotices } from './components/Notices'
import { useApplyItems } from './data'
import {
  clearDraft,
  describeError,
  draftKey,
  failureText,
  getDraft,
  idleWrites,
  isSignedOut,
  serialize,
  setDraft,
  writeReducer,
} from './writes'

export interface RunOptions<T> {
  /** for the failure text: "Couldn't <what>: <reason>" */
  what?: string
  /** writes sharing a serialize key go out one at a time, in order */
  serializeKey?: string
  onDone?: (result: T) => void
}

export function useWrite() {
  const [state, dispatch] = useReducer(writeReducer, idleWrites)
  const { notify, dismiss } = useNotices()
  const scope = useId()
  const noticeKey = useCallback((key: string) => `write:${scope}:${key}`, [scope])

  const run = useCallback(
    async <T,>(key: string, fn: () => Promise<T>, opts: RunOptions<T> = {}): Promise<boolean> => {
      dispatch({ type: 'start', key })
      try {
        const result = await (opts.serializeKey ? serialize(opts.serializeKey, fn) : fn())
        dispatch({ type: 'ok', key })
        dismiss(noticeKey(key))
        opts.onDone?.(result)
        return true
      } catch (e) {
        const message = describeError(e)
        dispatch({ type: 'fail', key, message })
        if (!isSignedOut(e)) {
          notify({
            key: noticeKey(key),
            tone: 'error',
            message: failureText(opts.what ?? 'save that', message),
            action: { label: 'Retry', run: () => void run(key, fn, opts) },
          })
        }
        return false
      }
    },
    [notify, dismiss, noticeKey],
  )

  return {
    run,
    /** true while any write (or the one under `key`) is in flight */
    busy: (key?: string) => (key ? state.saving.includes(key) : state.saving.length > 0),
    failure: state.failure,
    dismiss: () => dispatch({ type: 'dismiss' }),
  }
}

const FIELD_LABEL: Record<string, string> = {
  filename: 'the name',
  notes: 'the notes',
  rating: 'the rating',
  tags: 'the tags',
  folders: 'the folders',
  restore: 'restore it',
  retry: 'retry the preview',
}

/** Writes to one item, by field: ordered, applied in place, Retry on failure. */
export function useItemWrite(itemId: string) {
  const w = useWrite()
  const apply = useApplyItems()
  const write = useCallback(
    (field: string, fn: () => Promise<Item>, onSaved?: () => void) =>
      w.run(field, fn, {
        serializeKey: itemId,
        what: field === 'restore' || field === 'retry' ? FIELD_LABEL[field] : `save ${FIELD_LABEL[field] ?? field}`,
        onDone: (item) => {
          apply([item])
          onSaved?.()
        },
      }),
    [w.run, itemId, apply], // eslint-disable-line react-hooks/exhaustive-deps
  )
  return { write, busy: w.busy, failure: w.failure }
}

/** A text field saved on blur that never loses what you typed: unsaved text
 * is kept as a draft per item until the server has it. */
export function useDraftField(itemId: string, field: string, serverValue: string) {
  const key = draftKey(itemId, field)
  const [value, setValue] = useState(() => getDraft(key) ?? serverValue)
  // follow the server value unless there's unsaved text
  useEffect(() => {
    if (getDraft(key) === undefined) setValue(serverValue)
  }, [key, serverValue])
  const latest = useRef(serverValue)
  latest.current = serverValue
  const change = (v: string) => {
    setValue(v)
    if (v === latest.current) clearDraft(key)
    else setDraft(key, v)
  }
  return { value, change, dirty: value !== serverValue, saved: () => clearDraft(key) }
}
