// Phone layout helpers. The pure functions are tested in tests/mobile.test.ts.

import { useEffect, useState } from 'react'

/**
 * When the phone layout applies: narrow screens, plus phones held sideways
 * (short and touch-only). Keep in sync with the @media block in styles.css.
 */
const MOBILE_QUERY = '(max-width: 760px), (max-height: 500px) and (pointer: coarse)'

export type MobileCols = 2 | 3

export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches,
  )
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY)
    const onChange = () => setMobile(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return mobile
}

export type Swipe = 'left' | 'right' | 'down' | 'up' | null

/** Classify a finished touch drag. Short or diagonal drags are not swipes. */
export function swipeDirection(dx: number, dy: number, min = 50): Swipe {
  const ax = Math.abs(dx)
  const ay = Math.abs(dy)
  if (Math.max(ax, ay) < min) return null
  if (ax > ay * 1.5) return dx < 0 ? 'left' : 'right'
  if (ay > ax * 1.5) return dy < 0 ? 'up' : 'down'
  return null
}
