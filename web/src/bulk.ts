// The bulk-action engine: one action over many items, item by item, carrying
// on past failures. The result says which failed and why, so the UI can
// offer Retry for just those. Every request of one bulk action carries the
// same X-Kist-Batch id, so the whole action is one batch in the history and
// Undo reverts it in one step.

import { useMutation } from '@tanstack/react-query'
import { api, newBatchId, type Item } from './api'
import { useApplyItems } from './data'
import { runEach, serialize } from './writes'

export type BulkAction =
  | { kind: 'move'; folderId: string | null } // replace memberships
  | { kind: 'addFolder'; folderId: string }
  | { kind: 'trash' }
  | { kind: 'restore' }
  | { kind: 'hardDelete' }
  | { kind: 'addTags'; tags: string[] }
  | { kind: 'removeTag'; tag: string }
  | { kind: 'rating'; rating: number }
  | { kind: 'protect'; value: boolean }
  | { kind: 'flag'; flag: 'pick' | 'reject' | null }
  | { kind: 'label'; label: 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple' | null }

export interface BulkInput {
  ids: string[]
  action: BulkAction
}

export interface BulkResult {
  input: BulkInput
  ok: number
  failed: { id: string; error: string; retryable?: boolean }[]
  /** the X-Kist-Batch the action was sent as: POST /api/batches/{id}/undo */
  batch: string
}

export const BULK_VERB: Record<BulkAction['kind'], string> = {
  move: 'Moved',
  addFolder: 'Added',
  trash: 'Trashed',
  restore: 'Restored',
  hardDelete: 'Deleted',
  addTags: 'Tagged',
  removeTag: 'Untagged',
  rating: 'Rated',
  protect: 'Changed protection on',
  flag: 'Flagged',
  label: 'Labelled',
}

/** The one wording for permanent deletion, wherever it's asked. */
export function hardDeleteQuestion(n: number): string {
  return n === 1
    ? 'Permanently delete this item? This cannot be undone.'
    : `Permanently delete ${n} items? This cannot be undone.`
}

/** The notice after a trash, with Undo. */
export function trashedText(n: number): string {
  return n === 1 ? 'Moved 1 item to Trash.' : `Moved ${n} items to Trash.`
}

/** "Trashed 3 of 5. 2 failed: <first reason>." */
export function bulkReport(r: BulkResult): string {
  return `${BULK_VERB[r.input.action.kind]} ${r.ok} of ${r.ok + r.failed.length}. ${r.failed.length} failed: ${r.failed[0]?.error ?? 'unknown error'}.`
}

export function useBulkAction() {
  const apply = useApplyItems()
  return useMutation({
    mutationFn: async (input: BulkInput): Promise<BulkResult> => {
      const { ids, action } = input
      const removed: string[] = []
      const batch = newBatchId()
      const res = await runEach<Item | null>(ids, (id) =>
        serialize(id, async () => {
          switch (action.kind) {
            case 'move':
              return api.putFolders(id, action.folderId ? [action.folderId] : [], batch)
            case 'addFolder':
              return api.patchFolders(id, { add: [action.folderId] }, batch)
            case 'trash':
              return (await api.trashItem(id, batch)).item
            case 'restore':
              return api.restoreItem(id, batch)
            case 'hardDelete':
              await api.hardDeleteItem(id, batch)
              removed.push(id)
              return null
            case 'addTags':
              return api.patchTags(id, { add: action.tags }, batch)
            case 'removeTag':
              return api.patchTags(id, { remove: [action.tag] }, batch)
            case 'rating':
              return api.patchItem(id, { rating: action.rating }, batch)
            case 'protect':
              return api.patchItem(id, { protected: action.value }, batch)
            case 'flag':
              return api.patchItem(id, { flag: action.flag }, batch)
            case 'label':
              return api.patchItem(id, { label: action.label }, batch)
          }
        }),
      )
      apply(res.done.filter((i): i is Item => i !== null), removed)
      return { input, ok: res.done.length, failed: res.failed, batch }
    },
  })
}
