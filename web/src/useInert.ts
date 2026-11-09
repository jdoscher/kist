// Overlays (Modal, the preview, the phone menu, the token screen) make the
// rest of the page inert while open: Tab can't wander behind them, screen
// readers don't read what's underneath, and clicks can't reach it.
// Elements marked data-keep-live (the notices) stay usable.

import { useEffect, type RefObject } from 'react'

// several overlays can be open at once (a confirm over Needs attention):
// count holders per element so closing one doesn't wake the page under the other
const holders = new Map<Element, { n: number; had: boolean }>()

function hold(el: Element) {
  const h = holders.get(el)
  if (h) h.n++
  else {
    holders.set(el, { n: 1, had: el.hasAttribute('inert') })
    el.setAttribute('inert', '')
  }
}

function release(el: Element) {
  const h = holders.get(el)
  if (!h) return
  if (--h.n > 0) return
  holders.delete(el)
  if (!h.had) el.removeAttribute('inert')
}

/** Everything except `ref`'s element (and its ancestors) is inert while active. */
export function useInertBackground(ref: RefObject<HTMLElement | null>, active = true) {
  useEffect(() => {
    const el = ref.current
    if (!active || !el) return
    const held: Element[] = []
    let node: Element = el
    while (node.parentElement && node !== document.body) {
      const parent: Element = node.parentElement
      for (const sib of Array.from(parent.children)) {
        if (sib === node || sib.hasAttribute('data-keep-live') || sib.tagName === 'SCRIPT') continue
        hold(sib)
        held.push(sib)
      }
      node = parent
    }
    return () => held.forEach(release)
  }, [ref, active])
}

/** While active: focus `initial` (or the first focusable in `box`), and put
 * focus back where it was when it closes (`restore` can pick a better
 * target, e.g. the tile the preview ended on). */
export function useFocusInOut(
  box: RefObject<HTMLElement | null>,
  active: boolean,
  opts: { initial?: () => HTMLElement | null | undefined; restore?: () => HTMLElement | null | undefined } = {},
) {
  useEffect(() => {
    if (!active) return
    const before = document.activeElement as HTMLElement | null
    const target = opts.initial?.() ?? box.current?.querySelector<HTMLElement>(FOCUSABLE) ?? box.current
    target?.focus()
    return () => {
      const back = opts.restore?.()
      if (back && document.contains(back)) back.focus()
      else if (before && document.contains(before)) before.focus()
    }
  }, [active]) // eslint-disable-line react-hooks/exhaustive-deps
}

export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

/** Keep Tab and Shift+Tab inside `box` while active (the page behind is
 * inert, so without this Tab would leave for the browser's own controls). */
export function useFocusTrap(box: RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    const el = box.current
    if (!active || !el) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const items = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (n) => n.getClientRects().length > 0 || n === document.activeElement,
      )
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      const cur = document.activeElement
      if (e.shiftKey && (cur === first || !el.contains(cur))) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (cur === last || !el.contains(cur))) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [box, active])
}
