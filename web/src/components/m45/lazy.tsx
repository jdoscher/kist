// Lazy entry points for the Milestone 4/5 pieces that sit inside shared
// screens (the info panel, the item sheet, the preview), so the first page
// carries only these few lines.

import { lazy, Suspense } from 'react'
import type { Item } from '../../api'

const ItemSectionsImpl = lazy(() => import('./ItemSections'))

/** Media details and provenance for the info panel and the phone sheet. */
export function ItemSections({ item }: { item: Item }) {
  return (
    <Suspense fallback={null}>
      <ItemSectionsImpl item={item} />
    </Suspense>
  )
}

const MediaViewImpl = lazy(() => import('./MediaView'))

/** Kinds (and SVG) the Milestone 5 viewers show in the full-screen preview. */
export function hasMediaView(item: Pick<Item, 'kind' | 'ext'>): boolean {
  return ['video', 'audio', 'font', 'bundle', 'design', 'model'].includes(item.kind) || (item.kind === 'image' && item.ext.toLowerCase() === 'svg')
}

export function MediaView({ item, fallback }: { item: Item; fallback: React.ReactNode }) {
  return (
    <Suspense fallback={fallback}>
      <MediaViewImpl item={item} />
    </Suspense>
  )
}

const OnThisDayImpl = lazy(() => import('./TimelineScreen').then((m) => ({ default: m.OnThisDayCard })))

/** On this day, in the info panel when nothing is selected. */
export function OnThisDay() {
  return (
    <Suspense fallback={null}>
      <OnThisDayImpl compact />
    </Suspense>
  )
}
