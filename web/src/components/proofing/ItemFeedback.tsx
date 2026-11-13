// F3: guest feedback on one item, in the info panel and the phone item
// sheet (loaded lazily: lazy.tsx).

import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import type { Item } from '../../api'
import { groupByItem, proofApi } from '../../proofing'
import { FeedbackGroup } from './FeedbackGroup'
import { useCanProof, useFeedbackSummary } from './hooks'
import '../../proofing.css'

export default function ItemFeedback({ item }: { item: Pick<Item, 'id'> }) {
  const can = useCanProof()
  const summary = useFeedbackSummary()
  const any = (summary.data?.shares.length ?? 0) > 0
  const rows = useQuery({
    queryKey: ['feedback', 'item', item.id],
    queryFn: () => proofApi.list({ item_id: item.id, limit: 200 }),
    enabled: can && any,
    staleTime: 15_000,
  })
  const groups = useMemo(() => groupByItem(rows.data ?? []), [rows.data])
  if (!can || !groups.length) return null
  return (
    <section className="detail-section pf-item-section" aria-label="Guest feedback">
      <h3 className="section-label">Guest feedback</h3>
      {groups.map((g) => (
        <FeedbackGroup key={g.key} group={g} showItem={false} />
      ))}
    </section>
  )
}
