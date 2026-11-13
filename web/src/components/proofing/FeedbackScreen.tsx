// F3: the Feedback view (/feedback): guest picks, hearts and comments from
// share links, grouped per item, newest first, like an inbox. The header
// runs the link's proofing: rounds, show everyone's feedback, off.

import { useQuery } from '@tanstack/react-query'
import { MessageSquareHeart, RotateCcw, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { emptyQuery } from '../../api'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { groupByItem, proofApi, roundText, type ProofingShareSummary } from '../../proofing'
import { useNotices } from '../Notices'
import { FeedbackGroup } from './FeedbackGroup'
import { useCanProof, useFeedbackSummary, useRefreshFeedback } from './hooks'
import '../../proofing.css'

export default function FeedbackScreen({ shareId }: { shareId: string | null }) {
  const { openScreen, setQuery, selectOnly, mobile } = useAppState()
  const can = useCanProof()
  const summary = useFeedbackSummary()
  const refresh = useRefreshFeedback()
  const { notify } = useNotices()
  const shares = summary.data?.shares ?? []
  const share: ProofingShareSummary | undefined = shares.find((s) => s.share_id === shareId) ?? (shareId ? undefined : shares[0])
  const [round, setRound] = useState<number | 0>(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => setRound(0), [share?.share_id])

  const rows = useQuery({
    queryKey: ['feedback', 'list', share?.share_id ?? null, round],
    queryFn: () => proofApi.list({ share_id: share!.share_id, round: round || undefined }),
    enabled: can && !!share,
    refetchInterval: 30_000,
  })
  const groups = useMemo(() => groupByItem(rows.data ?? []), [rows.data])

  // looking is reading: this link's unseen count goes to 0
  const unseen = share?.unseen ?? 0
  const sid = share?.share_id
  useEffect(() => {
    if (!sid || !unseen || !rows.isSuccess) return
    const t = setTimeout(() => void proofApi.seen(sid).then(refresh, () => undefined), 1500)
    return () => clearTimeout(t)
  }, [sid, unseen, rows.isSuccess, refresh])

  const change = async (what: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await what()
      refresh()
      notify({ key: 'proof-settings', message: done })
    } catch (e) {
      notify({ key: 'proof-settings', tone: 'error', message: describeError(e) })
    } finally {
      setBusy(false)
    }
  }
  const openItem = (id: string) => {
    openScreen(null)
    setQuery({ ...emptyQuery })
    selectOnly(id)
  }

  if (!can) {
    return (
      <main className="pf-screen">
        <p className="pf-empty">Guest feedback needs a token that can manage share links.</p>
      </main>
    )
  }
  return (
    <main className={`pf-screen${mobile ? ' mobile' : ''}`} aria-labelledby="pf-h">
      <div className="pf-screen-head">
        {mobile && (
          <button className="icon-btn" onClick={() => openScreen(null)} aria-label="Back to the library">
            <X size={16} aria-hidden="true" />
          </button>
        )}
        <h1 id="pf-h" className="view-title">
          Feedback
        </h1>
        {shares.length > 0 && (
          <select
            aria-label="Share link"
            value={share?.share_id ?? ''}
            onChange={(e) => openScreen({ kind: 'feedback', share: e.target.value || null })}
          >
            {shares.map((s) => (
              <option key={s.share_id} value={s.share_id}>
                {(s.title || 'Untitled link') + (s.unseen ? ` (${s.unseen} new)` : '') + (s.status !== 'active' ? ` · ${s.status}` : '')}
              </option>
            ))}
          </select>
        )}
        {share && share.rounds.length > 1 && (
          <select aria-label="Round" value={round} onChange={(e) => setRound(Number(e.target.value))}>
            <option value={0}>All rounds</option>
            {[...share.rounds].reverse().map((r) => (
              <option key={r.n} value={r.n}>
                Round {r.n}
                {r.n === share.round ? ' (current)' : ''}
              </option>
            ))}
          </select>
        )}
        <span className="grow" />
        {share && <span className="pf-status">{roundText(share)}</span>}
      </div>
      {share && (
        <div className="pf-controls" role="group" aria-label="Guest feedback on this link">
          {share.enabled && (
            <>
              <button type="button" disabled={busy} onClick={() => void change(() => proofApi.newRound(share.share_id), `Round ${share.round + 1} started. Guests see a fresh page.`)}>
                <RotateCcw size={14} aria-hidden="true" /> Start round {share.round + 1}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void change(
                    () => proofApi.patch(share.share_id, { round_open: !share.round_open }),
                    share.round_open ? `Round ${share.round} closed. Guests can still look.` : `Round ${share.round} reopened.`,
                  )
                }
              >
                {share.round_open ? `Close round ${share.round}` : `Reopen round ${share.round}`}
              </button>
              <button
                type="button"
                aria-pressed={share.show_all}
                disabled={busy}
                onClick={() =>
                  void change(
                    () => proofApi.patch(share.share_id, { show_all: !share.show_all }),
                    share.show_all ? 'Guests now see only their own feedback.' : 'Guests now see everyone’s feedback.',
                  )
                }
              >
                Show everyone’s feedback to guests
              </button>
            </>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void change(
                () => proofApi.patch(share.share_id, { enabled: !share.enabled }),
                share.enabled ? 'Guest feedback is off for this link. What they gave stays here.' : 'Guests can pick and comment again.',
              )
            }
          >
            {share.enabled ? 'Turn guest feedback off' : 'Turn guest feedback on'}
          </button>
        </div>
      )}
      {summary.isLoading && <p className="pf-empty">Loading…</p>}
      {summary.isError && <p className="pf-empty error" role="alert">Couldn’t load guest feedback: {describeError(summary.error)}</p>}
      {summary.isSuccess && shares.length === 0 && (
        <div className="pf-empty">
          <MessageSquareHeart size={20} aria-hidden="true" />
          <h2>No guest feedback yet</h2>
          <p>
            Let guests pick, heart and comment when you share a folder, board or selection (“Let guests pick and comment” in the Share dialog,
            or under Settings → Sharing). Their feedback shows up here, and nothing changes in your library until you accept it.
          </p>
        </div>
      )}
      {share && rows.isError && <p className="pf-empty error" role="alert">{describeError(rows.error)}</p>}
      {share && rows.isSuccess && groups.length === 0 && (
        <p className="pf-empty">{share.enabled ? 'No feedback in this round yet.' : 'Guest feedback is off for this link.'}</p>
      )}
      <div className="pf-list">
        {groups.map((g) => (
          <FeedbackGroup key={g.key} group={g} onOpenItem={openItem} />
        ))}
      </div>
    </main>
  )
}
