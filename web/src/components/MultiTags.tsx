// The info panel with several items selected: every tag on any of them as a
// chip (with "3/5" when only some carry it); ✕ takes it off every item that
// has it, and the field adds tags (with matching existing tags offered as
// you type) to the items that miss them. Each change is one bulk action:
// one batch in the history, with Undo on its notice. Logic in multiTags.ts.

import { X } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { api, type GridItem } from '../api'
import { bulkReport, useBulkAction, type BulkAction } from '../bulk'
import { bulkDoneText } from '../bulkText'
import { useAfterUndo, useTags } from '../data'
import { undoReport } from '../history'
import { addTargets, removeLabel, shareText, sharedByAll, tagTally } from '../multiTags'
import { completeFragment, parseTagInput, tagSuggestions } from '../phoneEdit'
import { describeError } from '../writes'
import { TagText } from './ItemAI'
import { useNotices } from './Notices'

export function MultiTags({ items, selected }: { items: GridItem[]; selected: number }) {
  const bulk = useBulkAction()
  const { notify, dismiss } = useNotices()
  const afterUndo = useAfterUndo()
  const { data: allTags } = useTags()
  const [text, setText] = useState('')
  const uid = useId()
  const total = items.length
  const tally = useMemo(() => tagTally(items), [items])
  const shared = useMemo(() => sharedByAll(tally, total), [tally, total])
  const suggestions = useMemo(() => tagSuggestions(allTags ?? [], text, shared), [allTags, text, shared])
  const busy = bulk.isPending

  const undo = (batch: string) =>
    api.undoBatch(batch).then(
      (res) => {
        afterUndo()
        const rep = undoReport(res, 'Undid')
        if (rep.tone === 'error') notify({ key: 'bulk', tone: 'error', message: rep.message })
        else dismiss('bulk')
      },
      (e) => notify({ key: 'bulk', tone: 'error', message: `Couldn't undo: ${describeError(e)}`, action: { label: 'Retry', run: () => void undo(batch) } }),
    )

  const run = (action: BulkAction, ids: string[], onFail?: () => void) => {
    if (busy || !ids.length) return
    dismiss('bulk')
    bulk.mutate(
      { ids, action },
      {
        onSuccess: (r) => {
          if (r.failed.length) {
            const retry = r.failed.filter((f) => f.retryable !== false).map((f) => f.id)
            notify({
              key: 'bulk',
              tone: 'error',
              message: bulkReport(r),
              action: retry.length ? { label: 'Retry', run: () => run(action, retry) } : undefined,
              // what did go through is one batch: it can still be undone
              actions: r.ok ? [{ label: 'Undo', run: () => void undo(r.batch) }] : [],
            })
            if (!r.ok) onFail?.()
            return
          }
          const msg = bulkDoneText(action, r.ok)
          if (msg) notify({ key: 'bulk', message: msg, action: { label: 'Undo', run: () => void undo(r.batch) } })
        },
        onError: (e) => {
          notify({ key: 'bulk', tone: 'error', message: describeError(e) })
          onFail?.()
        },
      },
    )
  }

  const add = (value = text) => {
    const tags = parseTagInput(value)
    setText('')
    if (!tags.length) return
    const ids = addTargets(items, tags)
    if (!ids.length) {
      notify({ key: 'bulk', message: tags.length === 1 ? `All ${total} already have #${tags[0]}.` : `All ${total} already have those tags.` })
      return
    }
    run({ kind: 'addTags', tags }, ids, () => setText(value)) // typed text comes back to try again
  }

  return (
    <div className="detail-section mt-tags">
      <label htmlFor={`${uid}-tag`}>Tags</label>
      {selected > total && (
        <p className="hint mt-note">
          Showing the tags of the {total} loaded items; changes apply to those {total}.
        </p>
      )}
      {tally.length > 0 ? (
        <ul className="tag-chips" aria-label="Tags on the selected items">
          {tally.map((t) => {
            const share = shareText(t.count, total)
            return (
              <li
                key={t.tag}
                className={`chip${share ? ' mt-partial' : ''}`}
                title={share ? `On ${t.count} of the ${total} selected items` : `On all ${total} selected items`}
              >
                <TagText tag={t.tag} />
                {share && <span className="mt-share">{share}</span>}
                <button
                  aria-label={removeLabel(t, total)}
                  title={removeLabel(t, total)}
                  disabled={busy}
                  onClick={() => run({ kind: 'removeTag', tag: t.tag }, t.ids)}
                >
                  <X size={14} aria-hidden="true" />
                </button>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="hint mt-note">None of them has a tag yet.</p>
      )}
      <form
        className="mt-add"
        onSubmit={(e) => {
          e.preventDefault()
          add()
        }}
      >
        <input
          id={`${uid}-tag`}
          value={text}
          disabled={busy}
          autoComplete="off"
          placeholder={`Add tags to all ${total}, comma-sep, Enter`}
          aria-describedby={`${uid}-hint`}
          onChange={(e) => setText(e.target.value)}
        />
        <span id={`${uid}-hint`} className="sr-only">
          Separate several tags with commas. Matching tags are listed below the field.
        </span>
      </form>
      {suggestions.length > 0 && (
        <ul className="mt-suggest" aria-label="Matching tags">
          {suggestions.map((s) => (
            <li key={s}>
              <button type="button" disabled={busy} aria-label={`Add tag ${s} to the selected items`} onClick={() => add(completeFragment(text, s))}>
                <TagText tag={s} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <span className="saving" role="status">
        {busy ? 'Working…' : ''}
      </span>
    </div>
  )
}
