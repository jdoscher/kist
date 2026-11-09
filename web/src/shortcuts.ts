// The one owner of global keyboard shortcuts (no React; tests/shortcuts.test.ts).
//
// Components register handlers on a layer: 'grid' (the library view),
// 'preview' (the full-screen preview) or 'modal' (dialogs, the Needs
// attention panel, the token screen, the phone menu). Only the topmost open
// layer gets keys, so nothing behind an overlay reacts: Delete can't trash
// items while the preview or a dialog is open. Keys typed into a text
// field never reach any handler.

export type Layer = 'grid' | 'preview' | 'modal'

const RANK: Record<Layer, number> = { grid: 0, preview: 1, modal: 2 }

export interface KeyLike {
  key: string
  metaKey: boolean
  ctrlKey: boolean
  shiftKey: boolean
  altKey: boolean
  target: EventTarget | null
  defaultPrevented?: boolean
  preventDefault(): void
}

/** Return true when the key was used (it is then not offered to others). */
export type KeyHandler = (e: KeyLike) => boolean | void

interface ElementLike {
  closest?: (sel: string) => unknown
}

const TYPING = 'input, textarea, select, [contenteditable]:not([contenteditable="false"])'

/** Whether a key event comes from somewhere the user is typing. */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as ElementLike | null
  return !!el && typeof el.closest === 'function' && !!el.closest(TYPING)
}

export function createShortcuts() {
  const open: Layer[] = [] // open overlay layers, in no particular order
  const handlers: { layer: Layer; fn: KeyHandler }[] = []

  const top = (): Layer =>
    open.reduce<Layer>((best, l) => (RANK[l] > RANK[best] ? l : best), 'grid')

  return {
    /** Mark a layer open; returns the function that closes it again. */
    openLayer(layer: Layer): () => void {
      open.push(layer)
      let closed = false
      return () => {
        if (closed) return
        closed = true
        const i = open.indexOf(layer)
        if (i >= 0) open.splice(i, 1)
      }
    },
    topLayer: top,
    /** Handle keys while `layer` is on top; returns the unregister function. */
    on(layer: Layer, fn: KeyHandler): () => void {
      const entry = { layer, fn }
      handlers.push(entry)
      return () => {
        const i = handlers.indexOf(entry)
        if (i >= 0) handlers.splice(i, 1)
      }
    },
    /** Offer a key to the top layer's handlers, newest first. */
    dispatch(e: KeyLike): boolean {
      if (e.defaultPrevented || isTyping(e.target)) return false
      const layer = top()
      for (let i = handlers.length - 1; i >= 0; i--) {
        const h = handlers[i]
        if (h.layer === layer && h.fn(e)) return true
      }
      return false
    },
  }
}

export type Shortcuts = ReturnType<typeof createShortcuts>

/** The app's single registry; main.tsx installs its one keydown listener. */
export const shortcuts = createShortcuts()

export function installShortcuts(target: Pick<Window, 'addEventListener' | 'removeEventListener'>) {
  const onKey = (e: KeyboardEvent) => void shortcuts.dispatch(e)
  target.addEventListener('keydown', onKey)
  return () => target.removeEventListener('keydown', onKey)
}
