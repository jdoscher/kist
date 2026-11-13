// F3: one item's guest feedback (one share, one round) with the owner's
// Accept and Delete. Used by the Feedback view and the info panel.

import { Check, Heart, MessageSquare, Trash2, X } from 'lucide-react'
import { useCallback, useState } from 'react'
import { api } from '../../api'
import { describeError } from '../../writes'
import {
  acceptedText,
  markText,
  namesText,
  proofApi,
  versionNote,
  type AcceptAs,
  type ItemGroup,
} from '../../proofing'
import { useNotices } from '../Notices'
import { useRefreshItems } from './hooks'
import '../../proofing.css'

const LABELS = ['red', 'orange', 'yellow', 'green', 'blue', 'purple'] as const

function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return iso
  }
}

/** Accept with an Undo notice (one batch on the server). */
export function useAccept() {
  const { notify } = useNotices()
  const refresh = useRefreshItems()
  return useCallback(
    async (body: { mark_ids?: string[]; comment_ids?: string[]; as?: AcceptAs }) => {
      try {
        const r = await proofApi.accept(body)
        refresh()
        const batch = r.batch_id
        notify({
          key: 'proof-accept',
          message: acceptedText(r),
          ...(batch
            ? {
                action: {
                  label: 'Undo',
                  run: () =>
                    void api.undoBatch(batch).then(
                      () => {
                        refresh()
                        notify({ key: 'proof-accept', message: 'Undone.' })
                      },
                      (e) => notify({ key: 'proof-accept', tone: 'error', message: `Couldn't undo: ${describeError(e)}` }),
                    ),
                },
              }
            : {}),
        })
        return r
      } catch (e) {
        notify({ key: 'proof-accept', tone: 'error', message: `Couldn't accept: ${describeError(e)}` })
        return null
      }
    },
    [notify, refresh],
  )
}

export function FeedbackGroup({
  group,
  showItem = true,
  onOpenItem,
}: {
  group: ItemGroup
  showItem?: boolean
  onOpenItem?: (id: string) => void
}) {
  const accept = useAccept()
  const refresh = useRefreshItems()
  const { notify } = useNotices()
  const [busy, setBusy] = useState(false)
  const gone = group.item_state !== 'active'
  const name = group.item_filename ?? 'Item no longer in the library'
  const run = async (body: Parameters<typeof accept>[0]) => {
    setBusy(true)
    await accept(body)
    setBusy(false)
  }
  const removeComment = async (id: string) => {
    try {
      await proofApi.deleteComment(id)
      refresh()
    } catch (e) {
      notify({ tone: 'error', message: `Couldn't delete the comment: ${describeError(e)}` })
    }
  }
  const allPicks = [...new Map([...group.picks, ...group.hearts].map((m) => [m.id, m])).values()]
  const note = versionNote(group)
  const pickIds = group.openPicks.map((m) => m.id)
  return (
    <article className={`pf-group${group.unseen ? ' pf-unseen' : ''}`} aria-label={`${name}: guest feedback`}>
      {showItem && (
        <button type="button" className="pf-thumb" onClick={() => onOpenItem?.(group.item_id)} disabled={gone || !onOpenItem} title={gone ? undefined : 'Show in the library'}>
          {gone ? <span className="pf-thumb-missing">{group.item_state === 'trashed' ? 'In the trash' : 'Gone'}</span> : <img src={`/api/items/${group.item_id}/thumb?size=256`} alt="" loading="lazy" />}
        </button>
      )}
      <div className="pf-body">
        {showItem && (
          <div className="pf-head">
            <span className="pf-name">{name}</span>
            {group.unseen && <span className="pf-new">New</span>}
          </div>
        )}
        <div className="pf-meta">
          {group.share_title || 'Untitled link'} · round {group.round}
          {note && <> · {note}</>}
        </div>
        <ul className="pf-marks">
          {group.picks.length > 0 && (
            <li>
              <Check size={14} aria-hidden="true" /> Picked by {namesText(group.picks)}
            </li>
          )}
          {group.hearts.length > 0 && (
            <li>
              <Heart size={14} aria-hidden="true" /> Hearted by {namesText(group.hearts)}
            </li>
          )}
          {group.rejects.length > 0 && (
            <li>
              <X size={14} aria-hidden="true" /> Rejected by {namesText(group.rejects)}
            </li>
          )}
        </ul>
        {group.comments.length > 0 && (
          <ul className="pf-comments">
            {group.comments.map((c) => (
              <li key={c.id}>
                <div className="pf-comment-head">
                  <MessageSquare size={14} aria-hidden="true" />
                  <span className="pf-who">{c.guest_name}</span>
                  <span className="pf-when">{when(c.at)}</span>
                  {c.accepted && <span className="pf-done">Added as a note</span>}
                </div>
                <p className="pf-text">{c.text}</p>
                <div className="pf-actions">
                  {!c.accepted && !gone && (
                    <button type="button" disabled={busy} onClick={() => void run({ comment_ids: [c.id] })}>
                      Add as note
                    </button>
                  )}
                  <button type="button" className="icon-btn" aria-label={`Delete ${c.guest_name}'s comment`} title="Delete comment" onClick={() => void removeComment(c.id)}>
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {!gone && (group.picks.length > 0 || group.hearts.length > 0 || group.rejects.length > 0) && (
          <div className="pf-actions">
            {pickIds.length > 0 ? (
              <button type="button" disabled={busy} onClick={() => void run({ mark_ids: pickIds, as: 'pick' })}>
                <Check size={14} aria-hidden="true" /> Accept as pick
              </button>
            ) : (
              allPicks.length > 0 && <span className="pf-done">Picked</span>
            )}
            {group.rejects.some((r) => !r.accepted) && (
              <button type="button" disabled={busy} onClick={() => void run({ mark_ids: group.rejects.filter((r) => !r.accepted).map((r) => r.id), as: 'reject' })}>
                <X size={14} aria-hidden="true" /> Accept reject
              </button>
            )}
            {allPicks.length > 0 && (
              <select
                aria-label="Accept the picks as a colour label"
                value=""
                disabled={busy}
                onChange={(e) => {
                  const v = e.target.value
                  if (v) void run({ mark_ids: allPicks.map((m) => m.id), as: `label:${v}` })
                }}
              >
                <option value="">Accept as label…</option>
                {LABELS.map((l) => (
                  <option key={l} value={l}>
                    {l[0].toUpperCase() + l.slice(1)}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}
        <details className="pf-log">
          <summary>Who did what</summary>
          <ul>
            {[...group.picks, ...group.rejects, ...group.hearts]
              .filter((m, i, a) => a.findIndex((x) => x.id === m.id) === i)
              .map((m) => (
                <li key={m.id}>
                  {m.guest_name} {markText(m)} · {when(m.at)} · version {m.item_version}
                  {m.accepted && ' · accepted'}
                </li>
              ))}
          </ul>
        </details>
      </div>
    </article>
  )
}
