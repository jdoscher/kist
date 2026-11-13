// F3: "Let guests pick and comment" on a share link: the switch in the
// Share dialog (applied right after the link is made) and the row of
// controls under each link in Settings → Sharing.

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { proofApi, roundText } from '../../proofing'
import { useCanProof, useRefreshFeedback } from './hooks'
import '../../proofing.css'

export function ProofingOption({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  const can = useCanProof()
  if (!can) return null
  return (
    <fieldset className="share-fieldset">
      <legend>Feedback</legend>
      <label className="check-row">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} aria-describedby="pf-opt-hint" />
        Let guests pick and comment
      </label>
      <span id="pf-opt-hint" className="hint">
        Guests can pick, reject, heart and comment, with an optional name and no account. You see it under Feedback; nothing in your library
        changes until you accept it.
      </span>
    </fieldset>
  )
}

/** Turn proofing on for a link that was just created (the dialog's switch). */
export async function enableProofing(shareId: string): Promise<void> {
  await proofApi.patch(shareId, { enabled: true })
}

export function ShareProofingRow({ shareId, active }: { shareId: string; active: boolean }) {
  const can = useCanProof()
  const refresh = useRefreshFeedback()
  const { openScreen } = useAppState()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const q = useQuery({ queryKey: ['feedback', 'settings', shareId], queryFn: () => proofApi.settings(shareId), enabled: can })
  if (!can || !q.data) return null
  const s = q.data
  if (!active && !s.enabled && !s.marks && !s.comments) return null
  const patch = async (body: Parameters<typeof proofApi.patch>[1]) => {
    setBusy(true)
    setError('')
    try {
      await proofApi.patch(shareId, body)
      refresh()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  const total = s.marks + s.comments
  return (
    <div className="pf-share-row">
      {active && (
        <label className="check-row">
          <input type="checkbox" checked={s.enabled} disabled={busy} onChange={(e) => void patch({ enabled: e.target.checked })} />
          Let guests pick and comment
        </label>
      )}
      {active && s.enabled && (
        <label className="check-row">
          <input type="checkbox" checked={s.show_all} disabled={busy} onChange={(e) => void patch({ show_all: e.target.checked })} />
          Guests see everyone’s feedback
        </label>
      )}
      {(s.enabled || total > 0) && (
        <span className="hint">
          {roundText(s)}
          {total > 0 && ` · ${total} piece${total === 1 ? '' : 's'} of feedback${s.unseen ? `, ${s.unseen} new` : ''}`}
        </span>
      )}
      {(s.enabled || total > 0) && (
        <button type="button" onClick={() => openScreen({ kind: 'feedback', share: shareId })}>
          Open feedback
        </button>
      )}
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
