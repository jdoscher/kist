// React bindings for shortcuts.ts.

import { useEffect, useRef } from 'react'
import { shortcuts, type KeyHandler, type Layer } from './shortcuts'

/** Handle keys while `layer` is the top layer. The latest handler is always
 * used, so it can read current state without re-registering. */
export function useShortcut(layer: Layer, handler: KeyHandler, enabled = true) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (!enabled) return
    return shortcuts.on(layer, (e) => ref.current(e))
  }, [layer, enabled])
}

/** Mark an overlay layer open while `active` (keys behind it stop). */
export function useLayer(layer: Exclude<Layer, 'grid'>, active: boolean) {
  useEffect(() => {
    if (!active) return
    return shortcuts.openLayer(layer)
  }, [layer, active])
}
