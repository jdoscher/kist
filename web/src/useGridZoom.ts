// Grid zoom listeners (M6 W1.2 / W1.3): phone pinch steps the columns,
// desktop ⌘/Ctrl+wheel and trackpad pinch drive the tile size slider. The
// maths is in zoom.ts; the grid keeps the item under the fingers/pointer in
// place through `capture` (Grid.tsx's anchor).

import { useEffect, useRef } from 'react'
import { pinchCols, sliderStep, touchDistance, wheelSize, type PhoneCols } from './zoom.ts'

/** Something Grid can restore after the relayout (an item and where it was). */
export type Capture = (clientX: number, clientY: number) => void

/** Safari's non-standard gesture events (trackpad pinch on macOS, two-finger
 * pinch on iOS). */
interface GestureLike extends UIEvent {
  scale: number
  clientX: number
  clientY: number
}

/**
 * Phone: two fingers on the grid scale it live (a CSS transform on the
 * content, around the fingers) and snap to the nearest column step on
 * release. The browser's own page zoom is blocked over the grid only
 * (touch-action in m6grid.css plus these handlers).
 */
export function usePinchColumns(
  el: HTMLElement | null,
  content: () => HTMLElement | null,
  enabled: boolean,
  cols: PhoneCols,
  setCols: (c: PhoneCols) => void,
  capture: Capture,
) {
  const live = useRef({ cols, setCols, capture, content })
  live.current = { cols, setCols, capture, content }
  useEffect(() => {
    if (!el || !enabled) return
    let start: { dist: number; cols: PhoneCols; x: number; y: number } | null = null
    let scale = 1
    const reset = () => {
      const c = live.current.content()
      if (c) {
        c.style.transform = ''
        c.style.transformOrigin = ''
      }
      el.classList.remove('pinching')
      el.removeAttribute('data-pinch-cols')
    }
    const onStart = (e: TouchEvent) => {
      if (e.touches.length !== 2) return
      const [a, b] = [e.touches[0], e.touches[1]]
      const x = (a.clientX + b.clientX) / 2
      const y = (a.clientY + b.clientY) / 2
      start = { dist: Math.max(1, touchDistance(a, b)), cols: live.current.cols, x, y }
      scale = 1
      el.classList.add('pinching')
      // scale around the fingers, measured once (before any transform)
      const c = live.current.content()
      if (c) {
        const r = c.getBoundingClientRect()
        c.style.transformOrigin = `${x - r.left}px ${y - r.top}px`
      }
    }
    const onMove = (e: TouchEvent) => {
      if (!start || e.touches.length !== 2) return
      e.preventDefault()
      scale = touchDistance(e.touches[0], e.touches[1]) / start.dist
      const c = live.current.content()
      if (c) c.style.transform = `scale(${Math.min(3, Math.max(0.35, scale))})`
      el.setAttribute('data-pinch-cols', String(pinchCols(start.cols, scale)))
    }
    const onEnd = (e: TouchEvent) => {
      if (!start || e.touches.length >= 2) return
      const next = pinchCols(start.cols, scale)
      const { x, y } = start
      start = null
      // where the fingers were, before the transform comes off
      reset()
      if (next !== live.current.cols) {
        live.current.capture(x, y)
        live.current.setCols(next)
      }
    }
    const stopGesture = (e: Event) => e.preventDefault()
    el.addEventListener('touchstart', onStart, { passive: true })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd)
    el.addEventListener('touchcancel', onEnd)
    // iOS Safari zooms the page on a pinch unless its gesture events are stopped
    el.addEventListener('gesturestart', stopGesture)
    el.addEventListener('gesturechange', stopGesture)
    return () => {
      reset()
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onEnd)
      el.removeEventListener('gesturestart', stopGesture)
      el.removeEventListener('gesturechange', stopGesture)
    }
  }, [el, enabled])
}

/**
 * Desktop: ⌘/Ctrl + wheel over the grid (and a trackpad pinch, which
 * Chrome/Firefox/Edge send as a ctrlKey wheel and Safari as gesture events)
 * changes the tile size slider. Only over the grid: the browser's page zoom
 * still works everywhere else.
 */
export function useWheelSize(
  el: HTMLElement | null,
  enabled: boolean,
  size: number,
  setSize: (s: number) => void,
  capture: Capture,
) {
  const live = useRef({ size, setSize, capture, acc: size })
  live.current.size = size
  live.current.setSize = setSize
  live.current.capture = capture
  // the slider moved some other way: follow it
  if (Math.abs(live.current.acc - size) >= 10) live.current.acc = size
  useEffect(() => {
    if (!el || !enabled) return
    const apply = (raw: number, x: number, y: number) => {
      const L = live.current
      L.acc = raw
      const next = sliderStep(raw)
      if (next === L.size) return
      L.capture(x, y)
      L.size = next
      L.setSize(next)
    }
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return
      e.preventDefault()
      apply(wheelSize(live.current.acc, e.deltaY, e.deltaMode), e.clientX, e.clientY)
    }
    let gestureStart = 0
    const onGestureStart = (e: Event) => {
      e.preventDefault()
      gestureStart = live.current.acc
    }
    const onGestureChange = (e: Event) => {
      e.preventDefault()
      const g = e as GestureLike
      apply(Math.min(400, Math.max(120, gestureStart * g.scale)), g.clientX, g.clientY)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('gesturestart', onGestureStart)
    el.addEventListener('gesturechange', onGestureChange)
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('gesturestart', onGestureStart)
      el.removeEventListener('gesturechange', onGestureChange)
    }
  }, [el, enabled])
}
