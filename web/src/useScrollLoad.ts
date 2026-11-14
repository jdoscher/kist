// The grid's scroll speed as React state (P2): `fast` while tiles should
// hold their requests (scrollLoad.ts has the rules and the numbers).

import { useCallback, useEffect, useRef, useState } from 'react'
import { IDLE_MS, nextSpeed, stopped, type ScrollSpeed } from './scrollLoad'

export function useScrollLoad(el: HTMLElement | null): { fast: boolean; settle: () => void } {
  const [fast, setFast] = useState(false)
  const speed = useRef<ScrollSpeed | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    if (!el) return
    const onScroll = () => {
      const s = nextSpeed(speed.current, el.scrollTop, performance.now(), el.clientHeight)
      speed.current = s
      setFast(s.fast) // the same value is a no-op render-wise
      clearTimeout(timer.current)
      // stopped: what's on screen loads (timers, not animation frames,
      // which a hidden tab skips)
      if (s.fast) timer.current = setTimeout(onIdle, IDLE_MS)
    }
    const onIdle = () => {
      const s = speed.current
      // a busy main thread delays scroll events as much as this timer:
      // if the offset moved, it is still scrolling
      if (s && el.scrollTop !== s.top) return onScroll()
      if (s) speed.current = stopped(s)
      setFast(false)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      clearTimeout(timer.current)
      el.removeEventListener('scroll', onScroll)
    }
  }, [el])

  /** A jump landed (the date scrubber let go): load what's there now. */
  const settle = useCallback(() => {
    clearTimeout(timer.current)
    speed.current = null
    setFast(false)
  }, [])

  return { fast, settle }
}
