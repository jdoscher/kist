// Present (Kist 2.0 F2): keeping a show's images for offline use, through
// the service worker (public/sw.js PRESENT_CACHE / PRESENT_CLEAR).

export interface OfflineProgress {
  done: number
  failed: number
  total: number
  bytes: number
  finished: boolean
}

/** Offline needs the service worker controlling this page (a production
 * build on https or localhost, after one visit). */
export function offlineAvailable(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.serviceWorker?.controller
}

/** Ask the worker to keep these URLs (one show at a time, within the cap). */
export function keepOffline(urls: string[], capBytes: number, onProgress: (p: OfflineProgress) => void): boolean {
  const sw = navigator.serviceWorker?.controller
  if (!sw || !urls.length) return false
  const ch = new MessageChannel()
  ch.port1.onmessage = (e: MessageEvent) => {
    const m = e.data as { type: string; done: number; failed: number; total: number; bytes: number }
    onProgress({ done: m.done, failed: m.failed, total: m.total, bytes: m.bytes, finished: m.type === 'done' })
    if (m.type === 'done') ch.port1.close()
  }
  sw.postMessage({ type: 'PRESENT_CACHE', urls, capBytes }, [ch.port2])
  return true
}

/** Forget the offline show (frees the space). */
export function dropOffline(): void {
  navigator.serviceWorker?.controller?.postMessage({ type: 'PRESENT_CLEAR' })
}
