// P2: when grid tiles start loading their thumbnails (src/scrollLoad.ts).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { FAST_VPS, GESTURE_GAP_MS, IDLE_MS, isIdle, nextSpeed, SLOW_VPS, stopped, tileLoad, type ScrollSpeed } from '../src/scrollLoad.ts'

const VIEW = 900

/** Scroll events every `ms` ms moving `px` px each, from offset 0. */
function run(steps: number, px: number, ms: number, start: ScrollSpeed | null = null, t0 = 1000) {
  let s = start
  const out: ScrollSpeed[] = []
  for (let i = 0; i <= steps; i++) {
    s = nextSpeed(s, (start?.top ?? 0) + i * px, t0 + i * ms, VIEW)
    out.push(s)
  }
  return out
}

test('the first event of a gesture is never fast (a jump lands and loads)', () => {
  const s = nextSpeed(null, 50_000, 1000, VIEW)
  assert.equal(s.fast, false)
  assert.equal(s.v, 0)
  // a second jump after a pause is a new gesture too
  const s2 = nextSpeed(s, 0, 1000 + GESTURE_GAP_MS + 1, VIEW)
  assert.equal(s2.fast, false)
})

test('a fling at 600 px / 100 ms (6.7 viewports/s) is fast within two events', () => {
  const seq = run(10, 600, 100)
  assert.equal(seq[0].fast, false)
  assert.equal(seq[1].fast, true)
  assert.ok(seq.slice(1).every((s) => s.fast))
})

test('the same speed in frame-sized steps (96 px / 16 ms) is fast too', () => {
  const seq = run(20, 96, 16)
  assert.ok(seq.slice(2).every((s) => s.fast))
})

test('reading speed (a wheel notch now and then, ~1 viewport/s) never holds tiles', () => {
  // 100 px every 110 ms = 0.9 px/ms = 1 viewport/s at 900 px
  assert.ok(run(30, 100, 110).every((s) => !s.fast))
  // just under the threshold, continuous
  const px = ((FAST_VPS * 0.9 * VIEW) / 1000) * 16
  assert.ok(run(60, px, 16).every((s) => !s.fast))
})

test('hysteresis: a slowing fling stays fast until it drops below SLOW_VPS', () => {
  let s: ScrollSpeed | null = null
  let top = 0
  let t = 0
  const at = (vps: number, n: number) => {
    for (let i = 0; i < n; i++) {
      t += 16
      top += (vps * VIEW * 16) / 1000
      s = nextSpeed(s, top, t, VIEW)
    }
    return s as ScrollSpeed
  }
  assert.equal(at(5, 5).fast, true)
  // between the two thresholds: still fast
  assert.equal(at((FAST_VPS + SLOW_VPS) / 2, 10).fast, true)
  // below SLOW_VPS: loads again while still moving (momentum tail)
  assert.equal(at(SLOW_VPS * 0.5, 10).fast, false)
  // and between the thresholds again: stays loading until above FAST_VPS
  assert.equal(at((FAST_VPS + SLOW_VPS) / 2, 10).fast, false)
})

test('while fast, a long gap (busy main thread) is measured across, not a new gesture', () => {
  const seq = run(5, 600, 100)
  const last = seq.at(-1) as ScrollSpeed
  assert.equal(last.fast, true)
  // 300 ms without an event, 1800 px further: still a fling
  assert.equal(nextSpeed(last, last.top + 1800, last.t + 300, VIEW).fast, true)
  // once stopped (the idle timer ran), the next event starts a new gesture
  const s = nextSpeed(stopped(last), last.top + 50_000, last.t + 300, VIEW)
  assert.equal(s.fast, false)
  assert.equal(s.v, 0)
})

test('one jittery frame does not flip a slow scroll to fast', () => {
  // ~0.7 viewports/s, then one frame four times as long
  const seq = run(10, 10, 16)
  const last = seq.at(-1) as ScrollSpeed
  const j = nextSpeed(last, last.top + 40, last.t + 16, VIEW)
  assert.equal(j.fast, false)
})

test('direction does not matter', () => {
  const seq = run(5, -600, 100, { top: 100_000, t: 900, v: 0, fast: false })
  assert.ok(seq.at(-1)?.fast)
})

test('idle after IDLE_MS without a scroll event', () => {
  const s = nextSpeed(null, 0, 1000, VIEW)
  assert.equal(isIdle(s, 1000 + IDLE_MS - 1), false)
  assert.equal(isIdle(s, 1000 + IDLE_MS), true)
  assert.equal(isIdle(null, 0), true)
})

test('tileLoad: on screen high, overscan low, fast waits', () => {
  // viewport 1000..1900
  assert.equal(tileLoad(false, 1200, 1400, 1000, 900), 'high')
  // partly visible at either edge counts as on screen
  assert.equal(tileLoad(false, 900, 1001, 1000, 900), 'high')
  assert.equal(tileLoad(false, 1899, 2100, 1000, 900), 'high')
  // touching the edge is not on screen
  assert.equal(tileLoad(false, 800, 1000, 1000, 900), 'low')
  assert.equal(tileLoad(false, 1900, 2100, 1000, 900), 'low')
  assert.equal(tileLoad(true, 1200, 1400, 1000, 900), 'wait')
})

test('the grid wires it: placeholder while fast, cancel on unmount, priority', () => {
  const grid = readFileSync(new URL('../src/components/Grid.tsx', import.meta.url), 'utf8')
  assert.match(grid, /src=\{started\.current \? thumb : undefined\}/)
  assert.match(grid, /fetchPriority=\{load === 'high' \? 'high' : 'low'\}/)
  assert.match(grid, /removeAttribute\('src'\)/)
  // the scrubber's landing screen loads at once
  assert.match(grid, /settle\(\)\s*\n\s*scrub\.goTo\(pos\)/)
})
