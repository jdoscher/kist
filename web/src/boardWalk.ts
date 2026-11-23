// Stepping through a board's items in the full-screen preview (←/→): the
// library items on the board (images, videos, PDFs ...; notes, text, shapes
// and group frames are skipped) in reading order, rows top to bottom and
// left to right within a row. No React here: tests/boardWalk.test.ts.

import { useSyncExternalStore } from 'react'

export interface WalkNode {
  id: string
  type: string
  x: number
  y: number
  w: number
  h: number
  item_id?: string
}

/** Two boxes share a row when they overlap vertically by at least half
 * the shorter one's height: a slightly ragged row still reads as one, a
 * masonry column's next tile (mostly below) starts a new row. */
function sameRow(a: { y: number; h: number }, b: { y: number; h: number }): boolean {
  const overlap = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  return overlap >= 0.5 * Math.min(a.h, b.h)
}

/** The board's item ids in reading order, each item once (a second copy of
 * the same item on the board is skipped). */
export function boardWalkOrder(nodes: readonly WalkNode[]): string[] {
  const items = nodes.filter((n) => n.type === 'item' && !!n.item_id && n.h > 0)
  const byTop = [...items].sort((a, b) => a.y - b.y || a.x - b.x)
  const rows: WalkNode[][] = []
  for (const n of byTop) {
    // the row's first (topmost) box is its reference, so a row can't
    // drift downwards one overlapping box at a time
    const row = rows.find((r) => sameRow(r[0], n))
    if (row) row.push(n)
    else rows.push([n])
  }
  const out: string[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    row.sort((a, b) => a.x - b.x || a.y - b.y)
    for (const n of row) {
      if (seen.has(n.item_id!)) continue
      seen.add(n.item_id!)
      out.push(n.item_id!)
    }
  }
  return out
}

// ---------- which list the preview steps through ----------
// A board opens the preview with its own order; anywhere else the preview
// steps through the loaded grid. Cleared when the preview closes.

let walk: string[] | null = null
const subs = new Set<() => void>()

export function setPreviewWalk(ids: string[] | null) {
  walk = ids && ids.length ? ids : null
  for (const f of subs) f()
}

export function getPreviewWalk(): string[] | null {
  return walk
}

function subscribe(f: () => void) {
  subs.add(f)
  return () => subs.delete(f)
}

export function usePreviewWalk(): string[] | null {
  return useSyncExternalStore(subscribe, getPreviewWalk, getPreviewWalk)
}
