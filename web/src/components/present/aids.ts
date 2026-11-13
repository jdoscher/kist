// Present (Kist 2.0 F2): the hook F5 (viewing aids) plugs into.
//
// F5 sets `presentAids.wrap` to put its greyscale / posterise / mirror /
// grid / repeat-tile / true-100 % view around each slide's image, and may
// set `presentAids.Toolbar` (shown in the show's controls) and
// `presentAids.onKey` (keys the show doesn't use: it returns true when it
// took the key). View only: nothing an aid does is saved. With no F5 in the
// build the show renders the plain image.

import type { ComponentType, ReactNode } from 'react'
import type { SlideItem } from '../../present'

export interface PresentAids {
  wrap?: (image: ReactNode, item: SlideItem) => ReactNode
  Toolbar?: ComponentType<{ items: SlideItem[] }>
  onKey?: (e: KeyboardEvent | { key: string }) => boolean
}

export const presentAids: PresentAids = {}
