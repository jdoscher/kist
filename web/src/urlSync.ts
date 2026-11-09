// Keeps the address bar and browser history in step with where you are
// (the routes and Back rules are pure functions in url.ts).

import { useEffect, useRef } from 'react'
import { fromUrl, historyAction, layerOf, toUrl, type Place } from './url'

export function useUrlSync({
  initial,
  place,
  mobile,
  onNavigate,
  onMenu,
}: {
  /** where the app opened (read once from the URL) */
  initial: Place
  /** where the app is now */
  place: Place
  mobile: boolean
  /** Back/Forward went to another place: show it */
  onNavigate: (p: Place) => void
  /** Back/Forward opened or closed the phone menu layer */
  onMenu: (open: boolean) => void
}) {
  const url = toUrl(place)
  const shown = useRef<Place>(initial) // what the address bar shows now
  const afterBack = useRef<Place | null>(null)
  const handlers = useRef({ onNavigate, onMenu })
  handlers.current = { onNavigate, onMenu }

  useEffect(() => {
    // normalize the address once (e.g. /unfiled -> /, unknown paths -> /all)
    window.history.replaceState(window.history.state, '', toUrl(initial))
  }, [initial])

  useEffect(() => {
    const cur = shown.current
    const hs = window.history.state ?? {}
    const action = historyAction(cur, place, {
      mobile,
      curPushed: !!hs.kistLayer,
      curIsMenu: !!hs.kistMenu,
    })
    if (action === 'none') return
    const layered = layerOf(place, mobile) !== 'view'
    if (action === 'push') window.history.pushState({ kistLayer: layered }, '', url)
    else if (action === 'replace')
      window.history.replaceState({ kistLayer: !!hs.kistLayer && !hs.kistMenu && layered }, '', url)
    else {
      afterBack.current = place
      window.history.back()
    }
    shown.current = place
  }, [url, mobile]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onPop = () => {
      const hs = window.history.state ?? {}
      const target = afterBack.current
      afterBack.current = null
      handlers.current.onMenu(!!hs.kistMenu)
      if (target) {
        // we stepped back to close a layer; the app already shows `target`
        window.history.replaceState(
          { kistLayer: !!hs.kistLayer && layerOf(target, mobile) !== 'view' },
          '',
          toUrl(target),
        )
        shown.current = target
        return
      }
      const p = fromUrl(window.location.pathname, window.location.search)
      shown.current = p
      handlers.current.onNavigate(p)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [mobile])
}

/** Push/pop the phone menu as its own history layer (Back closes it). */
export function pushMenuLayer() {
  window.history.pushState({ kistMenu: true, kistLayer: true }, '', window.location.href)
}

export function inMenuLayer(): boolean {
  return !!window.history.state?.kistMenu
}
