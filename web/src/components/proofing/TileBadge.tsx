// F3: guest picks and hearts on a grid tile (current rounds only).

import { tileBadgeLabel, tileBadgeText } from '../../proofing'
import { useGuestCounts } from './hooks'
import '../../proofing.css'

export function GuestTileBadge({ itemId }: { itemId: string }) {
  const counts = useGuestCounts()
  const c = counts.data?.[itemId]
  const text = tileBadgeText(c)
  if (!text) return null
  return (
    <span className="badge pf-tile-badge" aria-hidden="true" title={tileBadgeLabel(c)}>
      {text}
    </span>
  )
}
