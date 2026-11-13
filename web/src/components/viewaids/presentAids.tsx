// F2 ↔ F5 (integration): the viewing aids inside Present, through Present's
// own hook (../present/aids.ts). Present keeps every plain key it uses
// (G grey/black surround, V presenter view, H help, T timer, digits go to a
// slide), so in a show the aids answer to Shift: Shift+G greyscale, Shift+V
// posterise, Shift+H mirror, Shift+L grid. A show is fit-only, so the
// repeat-tile and true-100 % aids are left out. View only: nothing is saved.
// Imported for its side effect by Show.tsx.

import type { ReactNode } from 'react'
import type { SlideItem } from '../../present'
import { presentAids } from '../present/aids'
import { viewAidKey, viewAidsStore, type AidFeatures } from '../../viewAids'
import { useViewAids, ViewAidsBadge, ViewAidsFrame, ViewAidsMenu } from './ViewAids'

/** Present has no repeat tile or 100 % view (a show always fits). */
export const PRESENT_FEATURES: AidFeatures = { tile: false, actual: false }

/** Shift+letter in a show → the aid's own key; null when it isn't one. */
export function presentAidKey(e: { key: string; shiftKey?: boolean; metaKey?: boolean; ctrlKey?: boolean; altKey?: boolean }): string | null {
  if (!e.shiftKey || e.metaKey || e.ctrlKey || e.altKey || e.key.length !== 1) return null
  const k = e.key.toLowerCase()
  return k === 'g' || k === 'v' || k === 'h' || k === 'l' ? k : null
}

/** A slide item as the frame wants it (cut-outs keep their transparency). */
export function frameItem(it: SlideItem) {
  return { ...it, has_thumb: !!it.thumb }
}

function PresentAidsFrame({ item, children }: { item: SlideItem; children: ReactNode }) {
  const [aids] = useViewAids()
  return (
    <ViewAidsFrame item={frameItem(item)} aids={aids} features={PRESENT_FEATURES} className="present-va">
      {children}
    </ViewAidsFrame>
  )
}

function PresentAidsToolbar() {
  const [aids, setAids] = useViewAids()
  return (
    <>
      <ViewAidsMenu aids={aids} setAids={setAids} features={PRESENT_FEATURES} className="present-va-menu" />
      <ViewAidsBadge aids={aids} features={PRESENT_FEATURES} onClear={() => setAids({ ...aids, grey: false, posterize: 0, mirror: false, grid: 'off' })} />
    </>
  )
}

presentAids.wrap = (image, item) => <PresentAidsFrame item={item}>{image}</PresentAidsFrame>
presentAids.Toolbar = PresentAidsToolbar
presentAids.onKey = (e) => {
  const k = presentAidKey(e as KeyboardEvent)
  if (!k) return false
  const next = viewAidKey({ key: k, metaKey: false, ctrlKey: false, altKey: false } as KeyboardEvent, viewAidsStore.get(), PRESENT_FEATURES)
  if (!next) return false
  ;(e as KeyboardEvent).preventDefault?.()
  viewAidsStore.set(next)
  return true
}
