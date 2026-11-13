// F3: the info panel's guest feedback section loads with its first use.

import { lazy, Suspense } from 'react'
import type { Item } from '../../api'
import { useCanProof } from './hooks'

const ItemFeedbackImpl = lazy(() => import('./ItemFeedback'))

export function ItemFeedback({ item }: { item: Pick<Item, 'id'> }) {
  const can = useCanProof()
  if (!can) return null
  return (
    <Suspense fallback={null}>
      <ItemFeedbackImpl item={item} />
    </Suspense>
  )
}
