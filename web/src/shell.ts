// The app shell around the library (Kist 2.0 Milestone 6, W2.2–W2.5): when
// the service worker registers, the Offline state, the icon badge and the
// iPhone "Add to Home Screen" hint. No React; tests/shell.test.ts. The
// worker itself is public/sw.js; the build fills in its file list
// (swBuild.ts, vite.config.ts).

// ---- service worker (W2.2) ----

/** Service workers need a secure origin: https, or this machine. */
export function isLocalhost(hostname: string): boolean {
  return (
    hostname === 'localhost' ||
    hostname.endsWith('.localhost') ||
    hostname === '[::1]' ||
    hostname === '::1' ||
    /^127(\.\d{1,3}){3}$/.test(hostname)
  )
}

/** Register only in a production build, on https or localhost, where the
 * browser has service workers at all (dev builds would cache Vite's
 * modules and hide edits). */
export function shouldRegisterSW(env: { prod: boolean; protocol: string; hostname: string; supported: boolean }): boolean {
  if (!env.prod || !env.supported) return false
  return env.protocol === 'https:' || (env.protocol === 'http:' && isLocalhost(env.hostname))
}

type Listener = () => void

/** A tiny store: a value plus change listeners (for useSyncExternalStore). */
export function createStore<T>(initial: T) {
  let value = initial
  const listeners = new Set<Listener>()
  return {
    get: () => value,
    set(next: T) {
      if (Object.is(next, value)) return
      value = next
      listeners.forEach((fn) => fn())
    },
    subscribe(fn: Listener) {
      listeners.add(fn)
      return () => void listeners.delete(fn)
    },
  }
}

/** A new version's worker is installed and waiting: the page shows
 * "Kist was updated" with Reload (the waiting worker takes over then). */
export interface WaitingWorker {
  postMessage(msg: unknown): void
}
export const swUpdate = createStore<WaitingWorker | null>(null)

export const SKIP_WAITING = { type: 'SKIP_WAITING' } as const
export const UPDATE_TEXT = 'Kist was updated'

let reloadAsked = false

/** Reload pressed: the waiting worker takes over, and the page reloads when
 * it has (main.tsx, controllerchange). Only then: a first install also
 * changes the controller, and must not reload the page under you. */
export function requestReload(worker: WaitingWorker) {
  reloadAsked = true
  worker.postMessage(SKIP_WAITING)
}

export const reloadRequested = () => reloadAsked

// ---- offline (W2.3) ----

/** Offline: the browser says so, or the last API request got no answer at
 * all (a network error, not an HTTP error). Any answer clears the second. */
export interface NetState {
  browserOnline: boolean
  networkFailed: boolean
}

export const netState = createStore<NetState>({
  browserOnline: typeof navigator === 'undefined' ? true : navigator.onLine !== false,
  networkFailed: false,
})

export function isOffline(s: NetState): boolean {
  return !s.browserOnline || s.networkFailed
}

/** api.ts calls this after every request: ok = the server answered
 * (any status), false = no answer. */
export function reportNetwork(ok: boolean, store = netState) {
  const s = store.get()
  if (s.networkFailed === !ok) return
  store.set({ ...s, networkFailed: !ok })
}

export function setBrowserOnline(online: boolean, store = netState) {
  const s = store.get()
  if (s.browserOnline === online) return
  // back online: the next request decides whether the server answers
  store.set({ browserOnline: online, networkFailed: online ? false : s.networkFailed })
}

/**
 * Call `fn` each time the state goes from offline to online. The server
 * coming back after an outage (a restart, a redeploy, the proxy) clears
 * the banner without the browser ever going offline, so React Query's own
 * refetch-on-reconnect never fires: whatever failed meanwhile (the grid,
 * the stats) would keep showing "Failed to load" until you navigate.
 * main.tsx refetches the failed queries here. Returns the unsubscribe.
 */
export function onReconnect(fn: () => void, store = netState): () => void {
  let was = isOffline(store.get())
  return store.subscribe(() => {
    const now = isOffline(store.get())
    if (was && !now) fn()
    was = now
  })
}

export const OFFLINE_TEXT = 'Offline. You can look at what’s already loaded; changes can’t be saved until the connection is back.'
export const UNREACHABLE_TEXT = 'Offline: Kist’s server isn’t answering. Changes can’t be saved until it does. Retrying…'

/** The banner text, or '' when online. No connection at all reads
 * differently from a server that doesn't answer. */
export function offlineText(s: NetState): string {
  if (!s.browserOnline) return OFFLINE_TEXT
  return s.networkFailed ? UNREACHABLE_TEXT : ''
}

// ---- icon badge (W2.4) ----

/** The Unfiled count for the badge: GET /api/stats' `unfiled` when the
 * server has it, else the total of GET /api/items?view=unfiled. */
export function unfiledCount(stats: { unfiled?: number | null } | undefined, viewTotal: number | null | undefined): number | null {
  if (stats && typeof stats.unfiled === 'number') return stats.unfiled
  return typeof viewTotal === 'number' ? viewTotal : null
}

export interface BadgeNavigator {
  setAppBadge?: (n?: number) => Promise<void>
  clearAppBadge?: () => Promise<void>
}

/** Show n on the app icon (cleared at 0). Feature-detected; a browser that
 * refuses (not installed, no permission) is ignored. Returns what it did. */
export async function applyBadge(nav: BadgeNavigator, n: number | null): Promise<'set' | 'cleared' | 'unsupported' | 'skipped' | 'failed'> {
  if (n === null) return 'skipped'
  if (typeof nav.setAppBadge !== 'function') return 'unsupported'
  try {
    if (n > 0) {
      await nav.setAppBadge(n)
      return 'set'
    }
    if (typeof nav.clearAppBadge === 'function') await nav.clearAppBadge()
    else await nav.setAppBadge(0)
    return 'cleared'
  } catch {
    return 'failed'
  }
}

// ---- iPhone install hint (W2.5) ----

export const INSTALL_HINT_ONCE = 'installHint'
export const INSTALL_HINT_TEXT = 'Add Kist to your Home Screen: Share → Add to Home Screen'

/** Mobile Safari on an iPhone/iPad (iPadOS says Macintosh but has touch);
 * other iOS browsers (Chrome, Firefox, Edge…) can't add to the Home Screen
 * the same way, so they get no hint. */
export function isIosSafari(ua: string, maxTouchPoints = 0): boolean {
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1)
  if (!ios) return false
  // Safari says Version/… Safari/…; desktop-class Chrome or Edge (a touch
  // Mac would pass the iPad test) and in-app web views don't
  if (!/AppleWebKit/.test(ua) || !/Version\/[\d.]+.*Safari\//.test(ua)) return false
  if (/Chrome\/|Chromium|Edg\//.test(ua)) return false
  return !/CriOS|FxiOS|EdgiOS|OPiOS|OPT\/|GSA\/|YaBrowser|DuckDuckGo|Brave/.test(ua)
}

export function shouldShowInstallHint(env: { ua: string; maxTouchPoints: number; standalone: boolean; dismissed: boolean }): boolean {
  return !env.dismissed && !env.standalone && isIosSafari(env.ua, env.maxTouchPoints)
}
