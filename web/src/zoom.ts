// Grid zoom: the phone's pinch density steps and the desktop's ⌘/Ctrl+wheel
// (and trackpad pinch) tile size. Pure functions; tests/zoom.test.ts.
// Grid.tsx owns the listeners (useGridZoom.ts).

import { TILE_SIZE_MAX, TILE_SIZE_MIN } from './layout.ts'

/** The phone grid's column steps. 1 column is the feed view. */
export const PHONE_COLS = [1, 2, 3, 4, 6] as const
export type PhoneCols = (typeof PHONE_COLS)[number]

export function isPhoneCols(n: unknown): n is PhoneCols {
  return (PHONE_COLS as readonly unknown[]).includes(n)
}

/** A stored column count, or the default (3) when it isn't one of the steps. */
export function parsePhoneCols(raw: string | null | undefined): PhoneCols {
  const n = Number(raw)
  return raw != null && isPhoneCols(n) ? n : 3
}

/**
 * The column step a pinch lands on. `scale` is the finger distance now over
 * the distance at the start: spreading (>1) makes tiles bigger, so fewer
 * columns. The step nearest `start / scale` (in log space, so 4->6 and 1->2
 * take the same finger travel) wins; small wobbles stay on the start step.
 */
export function pinchCols(start: PhoneCols, scale: number): PhoneCols {
  if (!Number.isFinite(scale) || scale <= 0) return start
  const target = Math.log(start / scale)
  let best: PhoneCols = start
  for (const c of PHONE_COLS) {
    if (Math.abs(Math.log(c) - target) < Math.abs(Math.log(best) - target)) best = c
  }
  return best
}

/** One step more (+1) or fewer (-1) columns, clamped to the ends. */
export function stepCols(cols: PhoneCols, dir: 1 | -1): PhoneCols {
  const i = PHONE_COLS.indexOf(cols)
  return PHONE_COLS[Math.min(PHONE_COLS.length - 1, Math.max(0, (i < 0 ? 2 : i) + dir))]
}

/** Distance between two touch points. */
export function touchDistance(a: { clientX: number; clientY: number }, b: { clientX: number; clientY: number }): number {
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)
}

/** Most a single wheel event moves the size, in wheel pixels: one mouse
 * notch (~100 px) is then one ~18% step rather than a jump to the end. */
const WHEEL_CLAMP = 20
const WHEEL_LINE = 20 // px per line when the wheel reports lines

/**
 * The tile size after one ⌘/Ctrl+wheel event (`ctrlKey` wheels are also how
 * Chrome and Firefox report a trackpad pinch). Chrome sends a pinch as
 * deltaY = -100·ln(scale), so exp(-deltaY/100) follows the fingers exactly.
 * Wheel up / fingers apart = bigger tiles. Returns the unrounded size so a
 * gesture made of many tiny events can accumulate.
 */
export function wheelSize(size: number, deltaY: number, deltaMode = 0): number {
  let dy = deltaMode === 1 ? deltaY * WHEEL_LINE : deltaMode === 2 ? deltaY * 400 : deltaY
  dy = Math.max(-WHEEL_CLAMP, Math.min(WHEEL_CLAMP, dy))
  return Math.min(TILE_SIZE_MAX, Math.max(TILE_SIZE_MIN, size * Math.exp(-dy / 100)))
}

/** A size as the slider shows it (its step is 10). */
export function sliderStep(size: number): number {
  return Math.min(TILE_SIZE_MAX, Math.max(TILE_SIZE_MIN, Math.round(size / 10) * 10))
}

/**
 * Where to scroll so a point stays over the same item after the layout
 * changed: the pointer was `pointY` px below the scroll box's top, `frac` of
 * the way down the item's tile; the item's row now starts at `rowStart`
 * (content px) and is `rowHeight` tall.
 */
export function anchoredScrollTop(rowStart: number, rowHeight: number, frac: number, pointY: number): number {
  const f = Math.min(1, Math.max(0, frac))
  return Math.max(0, Math.round(rowStart + f * rowHeight - pointY))
}
