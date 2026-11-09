// Grid <-> preview as a shared-element View Transition: the tile you open
// grows into the full-screen preview and shrinks back into place on close.
// Off when the browser lacks the API or the user asked for reduced motion
// (then the state change happens instantly, exactly as before).

import { flushSync } from 'react-dom'

export const HERO = 'kist-hero'

interface VTDoc {
  startViewTransition?: (cb: () => void | Promise<void>) => { finished: Promise<void> }
}

/** Whether to animate: the API exists and motion isn't reduced. */
export function canTransition(doc: VTDoc | undefined, reducedMotion: boolean): boolean {
  return !!doc && typeof doc.startViewTransition === 'function' && !reducedMotion
}

/** Which tile takes part: the item being opened, or the one being closed.
 * Moving between items inside the preview doesn't animate. */
export function heroFor(prev: string | null, next: string | null): string | null {
  if (prev === null && next !== null) return next
  if (prev !== null && next === null) return prev
  return null
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

function tileImage(id: string): HTMLElement | null {
  const tile = document.getElementById(`tile-${id}`)
  return (tile?.querySelector('img, .tile-icon') as HTMLElement | null) ?? null
}

function previewMedia(): HTMLElement | null {
  return document.querySelector('.preview-overlay figure > :first-child') as HTMLElement | null
}

const settle = (el: HTMLElement | null) =>
  el instanceof HTMLImageElement && !el.complete
    ? Promise.race([el.decode().catch(() => {}), new Promise((r) => setTimeout(r, 180))])
    : Promise.resolve()

/**
 * Run `update` (a React state change) inside a view transition that
 * morphs the tile of `heroId` into the preview, or back. `opening` says
 * which way.
 */
export function heroTransition(update: () => void, heroId: string | null, opening: boolean) {
  const doc = typeof document === 'undefined' ? undefined : (document as unknown as VTDoc)
  if (!heroId || !canTransition(doc, prefersReducedMotion())) {
    update()
    return
  }
  const from = opening ? tileImage(heroId) : previewMedia()
  if (!from) {
    update()
    return
  }
  from.style.viewTransitionName = HERO
  let to: HTMLElement | null = null
  const vt = doc!.startViewTransition!(async () => {
    from.style.viewTransitionName = ''
    flushSync(update)
    to = opening ? previewMedia() : tileImage(heroId)
    if (to) {
      to.style.viewTransitionName = HERO
      await settle(to)
    }
  })
  vt.finished.finally(() => {
    if (to) to.style.viewTransitionName = ''
  })
}
