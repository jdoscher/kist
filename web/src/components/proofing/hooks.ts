// F3: the owner's guest-feedback queries, shared by the sidebar count,
// the Feedback view, the info panel section and tile badges. Guest
// feedback needs a write token (like share links): readers never ask.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { useCaps } from '../../data'
import { proofApi } from '../../proofing'

export function useCanProof(): boolean {
  const caps = useCaps()
  return caps.ready && caps.canDestroy
}

export function useFeedbackSummary() {
  const can = useCanProof()
  return useQuery({ queryKey: ['feedback', 'summary'], queryFn: proofApi.summary, enabled: can, refetchInterval: 60_000, retry: false })
}

/** {item_id: counts} in every share's current round, for tiles. */
export function useGuestCounts() {
  const can = useCanProof()
  return useQuery({ queryKey: ['feedback', 'counts'], queryFn: proofApi.counts, enabled: can, refetchInterval: 60_000, staleTime: 30_000, retry: false })
}

export function useRefreshFeedback() {
  const qc = useQueryClient()
  return useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['feedback'] })
    void qc.invalidateQueries({ queryKey: ['shares'] })
  }, [qc])
}

/** After an accept (or its undo) the items changed: flags, labels, notes. */
export function useRefreshItems() {
  const qc = useQueryClient()
  return useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
    void qc.invalidateQueries({ queryKey: ['item'] })
    void qc.invalidateQueries({ queryKey: ['history'] })
    void qc.invalidateQueries({ queryKey: ['batches'] })
    void qc.invalidateQueries({ queryKey: ['annotations'] })
    void qc.invalidateQueries({ queryKey: ['feedback'] })
  }, [qc])
}
