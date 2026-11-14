// When the grid's tiles may start loading their thumbnails (Kist 2.0
// performance work, P2). Pure functions, tested in tests/scrollLoad.test.ts;
// the Grid wires them to its scroll events (useScrollLoad.ts).
//
// Measured on a 20k library (1440x900 @2x, tile size 160): a 3 s fast
// scroll started ~500 thumbnail requests (35 MB) for tiles that were on
// screen for a fraction of a second, and none of them were cancelled when
// their tile left. A tile crossing the screen at v viewports per second is
// visible for about 1/v s; above ~2 viewports/s that is shorter than a
// thumbnail takes to arrive and decode on a busy connection, so the bytes
// are wasted and they queue in front of the tiles you stop on.

/** Above this speed (viewports per second) tiles show their placeholder
 * and start no request. */
export const FAST_VPS = 2
/** ...and only below this one do they start again while still moving
 * (hysteresis, so a fling's slow tail doesn't flicker on and off). */
export const SLOW_VPS = 1
/** Scrolling has stopped once no scroll event came for this long. */
export const IDLE_MS = 120
/** A scroll event after a longer pause starts a new gesture: a single
 * jump (date scrubber, Home/End, a keyboard reveal) is not "fast", so the
 * screen it lands on loads at once. (While fast, a long gap is a busy main
 * thread, not a pause: the speed is measured across it.) */
export const GESTURE_GAP_MS = 200

export interface ScrollSpeed {
  /** the last scroll offset and when it was seen (ms) */
  top: number
  t: number
  /** smoothed speed in px per ms */
  v: number
  fast: boolean
}

/** The speed after a scroll event at offset `top`, time `t` (ms), with a
 * viewport `viewport` px tall. */
export function nextSpeed(prev: ScrollSpeed | null, top: number, t: number, viewport: number): ScrollSpeed {
  if (!prev || (!prev.fast && t - prev.t > GESTURE_GAP_MS)) return { top, t, v: 0, fast: false }
  const dt = Math.max(1, t - prev.t)
  const inst = Math.abs(top - prev.top) / dt
  // a short exponential average: one jittery frame doesn't flip the state
  const v = prev.v === 0 ? inst : prev.v * 0.5 + inst * 0.5
  const vps = (v * 1000) / Math.max(1, viewport)
  const fast = prev.fast ? vps > SLOW_VPS : vps > FAST_VPS
  return { top, t, v, fast }
}

/** Scrolling stopped: the state a later event starts from. */
export function stopped(s: ScrollSpeed): ScrollSpeed {
  return { ...s, v: 0, fast: false }
}

/** True once scrolling has been still for IDLE_MS. */
export function isIdle(s: ScrollSpeed | null, now: number): boolean {
  return !s || now - s.t >= IDLE_MS
}

export type TileLoad = 'high' | 'low' | 'wait'

/** How a tile in row [rowStart, rowEnd) loads, given the scroll window
 * [top, top + viewport): on screen first (fetchpriority high), the
 * overscan rows after (low), nothing during a fast scroll. */
export function tileLoad(fast: boolean, rowStart: number, rowEnd: number, top: number, viewport: number): TileLoad {
  if (fast) return 'wait'
  return rowEnd > top && rowStart < top + viewport ? 'high' : 'low'
}
