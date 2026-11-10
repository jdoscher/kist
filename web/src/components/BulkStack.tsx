// Stack / Unstack in the bulk bar. Each is one batch, so the notice's Undo
// reverts it.

import { Layers, Layers2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api, newBatchId } from '../api'
import { useCurrentItems } from '../data'
import { org } from '../organiseApi'
import { canStack, stacksOf } from '../stacks'
import { useAppState } from '../state'
import { describeError } from '../writes'
import { useAfterStackChange } from './GridExtras'
import { useNotices } from './Notices'

export function StackActions({ disabled }: { disabled?: boolean }) {
  const { selection, selectAll } = useAppState()
  const { items } = useCurrentItems()
  const { notify } = useNotices()
  const after = useAfterStackChange()
  const [busy, setBusy] = useState(false)
  // in grid order, so the first selected item becomes the cover
  const selected = useMemo(() => items.filter((i) => selection.has(i.id)), [items, selection])
  const stacks = stacksOf(selected)
  const ids = useMemo(() => [...selection], [selection])

  const undo = (batch: string) => () =>
    void api.undoBatch(batch).then(after, (e) =>
      notify({ key: 'stack', tone: 'error', message: `Couldn't undo: ${describeError(e)}` }),
    )

  const stack = async () => {
    const batch = newBatchId()
    setBusy(true)
    try {
      // members of a collapsed stack come along with their entry
      const cover = selected[0]?.id
      const st = await org.createStack(ids, cover, batch)
      after()
      selectAll([st.cover_item_id])
      notify({ key: 'stack', message: `Stacked ${st.count} items. The first one is the cover.`, action: { label: 'Undo', run: undo(batch) } })
    } catch (e) {
      notify({ key: 'stack', tone: 'error', message: `Couldn't stack them: ${describeError(e)}` })
    } finally {
      setBusy(false)
    }
  }

  const unstack = async () => {
    const batch = newBatchId()
    setBusy(true)
    try {
      let n = 0
      for (const sid of stacks) n += (await org.unstack(sid, batch)).unstacked
      after()
      notify({
        key: 'stack',
        message: `Unstacked ${stacks.length === 1 ? 'the stack' : `${stacks.length} stacks`} (${n} items).`,
        action: { label: 'Undo', run: undo(batch) },
      })
    } catch (e) {
      notify({ key: 'stack', tone: 'error', message: `Couldn't unstack: ${describeError(e)}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {canStack(selected) && (
        <button className="btn-icon" disabled={disabled || busy} onClick={() => void stack()} title="Show these as one item, the first as its cover">
          <Layers size={14} strokeWidth={1.75} aria-hidden="true" />
          Stack
        </button>
      )}
      {stacks.length > 0 && (
        <button className="btn-icon" disabled={disabled || busy} onClick={() => void unstack()} title="Show every item of the stack on its own again">
          <Layers2 size={14} strokeWidth={1.75} aria-hidden="true" />
          Unstack
        </button>
      )}
    </>
  )
}
