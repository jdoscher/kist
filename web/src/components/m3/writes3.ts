// Small write helpers shared by triage, survey, compare and the info panel:
// one item edit as its own batch (so it can be undone by batch), applied in
// place everywhere, with Retry on failure.

import { useCallback } from 'react'
import { api, newBatchId, type Item } from '../../api'
import { useApplyItems } from '../../data'
import { useWrite } from '../../useWrite'

export type ItemPatch = Partial<Pick<Item, 'rating' | 'flag' | 'label' | 'filename' | 'notes'>>

const WHAT: Record<string, string> = {
  flag: 'save the flag',
  label: 'save the label',
  rating: 'save the rating',
}

/** patch(item, {flag: 'pick'}) -> the batch id it was sent as (null when it failed). */
export function usePatchItem() {
  const w = useWrite()
  const apply = useApplyItems()
  const patch = useCallback(
    async (item: Pick<Item, 'id'>, change: ItemPatch): Promise<string | null> => {
      const batch = newBatchId()
      const key = `${item.id}:${Object.keys(change).join(',')}`
      const ok = await w.run(key, () => api.patchItem(item.id, change, batch), {
        serializeKey: item.id,
        what: WHAT[Object.keys(change)[0]] ?? 'save that',
        onDone: (it) => apply([it]),
      })
      return ok ? batch : null
    },
    [w.run, apply], // eslint-disable-line react-hooks/exhaustive-deps
  )
  return { patch, busy: w.busy }
}

/** Tags and folders, as one batch each (add only: never overwrites). */
export function useFileItem() {
  const w = useWrite()
  const apply = useApplyItems()
  const addTags = useCallback(
    async (item: Pick<Item, 'id'>, tags: string[]) => {
      const batch = newBatchId()
      const ok = await w.run(`${item.id}:tags`, () => api.patchTags(item.id, { add: tags }, batch), {
        serializeKey: item.id,
        what: 'add the tags',
        onDone: (it) => apply([it]),
      })
      return ok ? batch : null
    },
    [w.run, apply], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const addFolder = useCallback(
    async (item: Pick<Item, 'id'>, folderId: string) => {
      const batch = newBatchId()
      const ok = await w.run(`${item.id}:folders`, () => api.patchFolders(item.id, { add: [folderId] }, batch), {
        serializeKey: item.id,
        what: 'add it to the folder',
        onDone: (it) => apply([it]),
      })
      return ok ? batch : null
    },
    [w.run, apply], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const trash = useCallback(
    async (item: Pick<Item, 'id'>) => {
      const batch = newBatchId()
      const ok = await w.run(`${item.id}:trash`, () => api.trashItem(item.id, batch), {
        serializeKey: item.id,
        what: 'move it to Trash',
        onDone: (r) => apply([r.item]),
      })
      return ok ? batch : null
    },
    [w.run, apply], // eslint-disable-line react-hooks/exhaustive-deps
  )
  return { addTags, addFolder, trash, busy: w.busy }
}
