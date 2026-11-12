// Motion: the JS side of the motion tokens in styles.css (--dur-in,
// --dur-out). Things that appear fade and slide in with a CSS animation on
// mount; to animate a close, a component stays mounted for --dur-out with a
// `leaving` class (usePresence). Reduced motion closes at once.

import { useEffect, useState } from 'react'

/** --dur-in / --dur-out in ms (keep in step with styles.css :root). */
export const DUR_IN_MS = 120
export const DUR_OUT_MS = 80

export function prefersReducedMotion(win: { matchMedia?: (q: string) => { matches: boolean } } | undefined = typeof window === 'undefined' ? undefined : window): boolean {
  return !!win?.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** How long a closing element stays mounted to play its exit. */
export function exitDelay(reduced: boolean, ms = DUR_OUT_MS): number {
  return reduced ? 0 : ms
}

/**
 * `mounted` stays true for the exit duration after `open` turns false,
 * with `leaving` true meanwhile, so CSS can fade the element out.
 */
export function usePresence(open: boolean, ms = DUR_OUT_MS): { mounted: boolean; leaving: boolean } {
  const [mounted, setMounted] = useState(open)
  useEffect(() => {
    if (open) {
      setMounted(true)
      return
    }
    const delay = exitDelay(prefersReducedMotion(), ms)
    if (!delay) {
      setMounted(false)
      return
    }
    const t = setTimeout(() => setMounted(false), delay)
    return () => clearTimeout(t)
  }, [open, ms])
  return { mounted: open || mounted, leaving: !open && mounted }
}
