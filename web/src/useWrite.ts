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

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useReducer, useRef, useState } from 'react'
import { api, conflictItem, type Item } from './api'
import { useNotices } from './components/Notices'
import { useApplyItems } from './data'
import {
  clearDraft,
  clearDraftIf,
  describeError,
  discardDraft,
  draftKey,
  failureText,
  getDraft,
  idleWrites,
  isFolderGone,
  isSignedOut,
  laterStamp,
  onDiscard,
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
  /** return true when the error was handled (no failure notice) */
  onError?: (e: unknown) => boolean
}

export function useWrite() {
  const [state, dispatch] = useReducer(writeReducer, idleWrites)
  const { notify, dismiss } = useNotices()
  const qc = useQueryClient()
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
        // a folder offered on screen was deleted elsewhere: reload the list
        if (isFolderGone(e)) void qc.invalidateQueries({ queryKey: ['folders'] })
        if (opts.onError?.(e)) return false
        if (!isSignedOut(e)) {
          notify({
            key: noticeKey(key),
            tone: 'error',
            message: failureText(opts.what ?? 'save that', message),
            // retrying a write to a deleted folder can't help
            action: isFolderGone(e) ? undefined : { label: 'Retry', run: () => void run(key, fn, opts) },
          })
        }
        return false
      }
    },
    [notify, dismiss, noticeKey, qc],
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
    (field: string, fn: () => Promise<Item>, onSaved?: () => void, onError?: (e: unknown) => boolean) =>
      w.run(field, fn, {
        serializeKey: itemId,
        what: field === 'restore' || field === 'retry' ? FIELD_LABEL[field] : `save ${FIELD_LABEL[field] ?? field}`,
        onDone: (item) => {
          apply([item])
          onSaved?.()
        },
        onError,
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
  // "Use theirs" threw the draft away: show the server's text
  useEffect(() => onDiscard(key, setValue), [key])
  const latest = useRef(serverValue)
  latest.current = serverValue
  const change = (v: string) => {
    setValue(v)
    if (v === latest.current) clearDraft(key)
    else setDraft(key, v)
  }
  return {
    value,
    change,
    dirty: value !== serverValue,
    /** the save of `sent` (the field's text when it was sent) landed: the
     * draft goes only if nothing was typed since */
    saved: (sent: string) => clearDraftIf(key, sent),
  }
}

const TEXT_LABEL = { notes: 'notes', filename: 'name' } as const

/**
 * Notes and filename saves with optimistic concurrency (Kist 1.5 contract
 * item 1): each sends base_modified_at, the newest modified_at this tab has
 * for the item. If someone else (another tab, an agent) changed the item
 * since, the server answers 409 with its copy and a notice offers
 * "Keep mine" (send again on top of their version) or "Use theirs" (show
 * the server's text and drop the draft).
 */
export function useTextSave(item: Item) {
  const { write } = useItemWrite(item.id)
  const qc = useQueryClient()
  const apply = useApplyItems()
  const { notify, dismiss } = useNotices()
  const ref = useRef(item)
  ref.current = item

  const save = useCallback(
    (field: 'notes' | 'filename', value: string, onSaved?: (sent: string) => void): Promise<boolean> => {
      const id = item.id
      const key = draftKey(id, field)
      const conflictKey = `conflict:${key}`
      // read at send time: an earlier write to this item (serialized) may
      // have moved modified_at on
      const base = () => laterStamp(ref.current.modified_at, qc.getQueryData<Item>(['item', id])?.modified_at)
      const send = (text: string): Promise<boolean> =>
        write(
          field,
          // `text` is the field as typed (what the draft holds); filenames
          // go out trimmed
          () =>
            api.patchItem(id, {
              ...(field === 'filename' ? { filename: text.trim() } : { notes: text }),
              base_modified_at: base(),
            }),
          () => {
            dismiss(conflictKey)
            onSaved?.(text)
          },
          (e) => {
            const theirs = conflictItem(e)
            if (!theirs) return false
            // their version everywhere; the draft keeps your text on screen
            apply([theirs])
            notify({
              key: conflictKey,
              tone: 'error',
              message: `Changed elsewhere: “${theirs.filename}” was changed in another tab or by an agent while you edited its ${TEXT_LABEL[field]}.`,
              actions: [
                { label: 'Keep mine', run: () => void send(getDraft(key) ?? text) },
                { label: 'Use theirs', run: () => discardDraft(key, theirs[field]) },
              ],
            })
            return true
          },
        )
      return send(value)
    },
    [item.id, write, qc, apply, notify, dismiss],
  )
  return save
}
