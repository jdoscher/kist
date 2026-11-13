/* Kist's service worker (Kist 2.0 Milestone 6, W2.2). Hand-written, no
 * Workbox. It keeps the app shell (index.html, the hashed Vite bundle,
 * icons) so Kist opens without a connection and says it is offline. It
 * never touches the library: /api/* (item files, thumbnails, every query),
 * /mcp, the share pages (/s/...) and the API docs always go to the network
 * and are never stored.
 *
 * One exception, asked for by the person presenting (Kist 2.0 F2): when a
 * Present show starts with "Keep it offline", the page sends PRESENT_CACHE
 * with the show's image URLs (they carry `present=1`) and a size cap, and
 * the worker stores them in the separate "kist-present" cache, one show at
 * a time. Only those URLs are answered from it (cache first, then the
 * network); PRESENT_CLEAR deletes it. Nothing else under /api is touched.
 *
 * The build (vite.config.ts → src/swBuild.ts) replaces the BUILD marker
 * below with this build's version and file list, so every build has its
 * own cache name and old caches are deleted when it takes over. A new
 * version installs in the background and then waits: the page shows
 * "Kist was updated" with Reload, which tells it to take over.
 */

const BUILD = /*__KIST_BUILD__*/ { version: 'dev', assets: [] } /*__KIST_BUILD_END__*/

const PREFIX = 'kist-shell-'
const CACHE = PREFIX + BUILD.version
const PRECACHED = new Set(BUILD.assets)

/** Path prefixes the worker leaves alone (network only, never cached):
 * the API (item files and thumbnails are under it), MCP, share pages,
 * the API docs and the worker itself. */
const NEVER = ['api', 'mcp', 's', 'docs', 'redoc', 'openapi.json', 'sw.js', 'present-relay']
const PRESENT_CACHE = 'kist-present'

/** A Present image (2.0 F2): an item's file or thumbnail asked for with
 * present=1. Only these come from the offline show cache. */
function isPresentImage(url, origin) {
  return (
    url.origin === origin &&
    /^\/api\/items\/[^/]+\/(file|thumb)$/.test(url.pathname) &&
    url.searchParams.get('present') === '1'
  )
}

function bypass(url, origin) {
  if (url.origin !== origin) return true
  const first = url.pathname.split('/')[1] || ''
  return NEVER.includes(first)
}

/** Hashed bundle files: safe to keep for good under their own name. */
function isAsset(url) {
  return url.pathname.startsWith('/assets/')
}

/** What a request is: 'network' (not ours), 'page' (index.html, network
 * first), 'asset' (cache first) or 'static' (a precached root file). */
function route(request, origin) {
  if (request.method !== 'GET') return 'network'
  const url = new URL(request.url)
  if (isPresentImage(url, origin)) return 'present'
  if (bypass(url, origin)) return 'network'
  if (request.mode === 'navigate') return 'page'
  if (isAsset(url)) return 'asset'
  if (PRECACHED.has(url.pathname + url.search)) return 'static'
  return 'network'
}

self.__kistSw = { BUILD, CACHE, PRESENT_CACHE, bypass, route }

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // straight from the server, not the HTTP cache: the shell must be
      // this build's
      cache.addAll(BUILD.assets.map((a) => new Request(a, { cache: 'reload' }))),
    ),
  )
  // no skipWaiting(): an open tab keeps the version it loaded until the
  // person presses Reload on "Kist was updated"
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith(PREFIX) && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting()
  if (event.data && event.data.type === 'PRESENT_CACHE') {
    const port = event.ports && event.ports[0]
    event.waitUntil(cachePresent(event.data.urls || [], Number(event.data.capBytes) || 0, port))
  }
  if (event.data && event.data.type === 'PRESENT_CLEAR') event.waitUntil(caches.delete(PRESENT_CACHE))
})

/** Keep one Present show's images for offline use, within capBytes; drop
 * the previous show's. Progress goes back over the message port. */
async function cachePresent(urls, capBytes, port) {
  const say = (m) => {
    try {
      if (port) port.postMessage(m)
    } catch (e) {
      /* the page went away */
    }
  }
  const cache = await caches.open(PRESENT_CACHE)
  const wanted = new Set(urls)
  for (const req of await cache.keys()) {
    const u = new URL(req.url)
    if (!wanted.has(u.pathname + u.search)) await cache.delete(req)
  }
  let bytes = 0
  let done = 0
  let failed = 0
  for (const u of urls) {
    try {
      let res = await cache.match(u)
      if (!res) {
        res = await fetch(u, { credentials: 'same-origin' })
        if (!res.ok || res.type !== 'basic') {
          failed++
          continue
        }
        const size = Number(res.headers.get('Content-Length')) || 0
        if (capBytes && bytes + size > capBytes) break
        await cache.put(u, res.clone())
        bytes += size
      } else {
        bytes += Number(res.headers.get('Content-Length')) || 0
      }
      done++
    } catch (e) {
      failed++
    }
    say({ type: 'progress', done, failed, total: urls.length, bytes })
  }
  say({ type: 'done', done, failed, total: urls.length, bytes })
}

self.addEventListener('fetch', (event) => {
  const kind = route(event.request, self.location.origin)
  if (kind === 'network') return
  if (kind === 'present') {
    // a kept Present image, else the network (and nothing is stored here:
    // only PRESENT_CACHE fills the cache, within its cap). A range request
    // (video) always goes to the network.
    if (event.request.headers && event.request.headers.get && event.request.headers.get('range')) return
    event.respondWith(
      caches
        .open(PRESENT_CACHE)
        .then((c) => c.match(event.request))
        .then((hit) => hit || fetch(event.request)),
    )
    return
  }
  if (kind === 'page') {
    // the page: always the server's when it answers (a redeploy shows at
    // once); the kept copy only without a connection. The kept copy is not
    // replaced here: it belongs with the bundle files this version keeps.
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match('/', { cacheName: CACHE }).then((r) => r || Response.error()),
      ),
    )
    return
  }
  event.respondWith(
    caches.match(event.request, { cacheName: CACHE }).then((hit) => {
      if (hit) return hit
      return fetch(event.request).then((res) => {
        // a bundle file this build didn't list (or a newer build's, while
        // the old worker still runs): keep it once it arrived whole
        if (kind === 'asset' && res.ok && res.type === 'basic') {
          const copy = res.clone()
          void caches.open(CACHE).then((c) => c.put(event.request, copy))
        }
        return res
      })
    }),
  )
})
