/* Kist's service worker (Kist 2.0 Milestone 6, W2.2). Hand-written, no
 * Workbox. It keeps the app shell (index.html, the hashed Vite bundle,
 * icons) so Kist opens without a connection and says it is offline. It
 * never touches the library: /api/* (item files, thumbnails, every query),
 * /mcp, the share pages (/s/...) and the API docs always go to the network
 * and are never stored.
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
const NEVER = ['api', 'mcp', 's', 'docs', 'redoc', 'openapi.json', 'sw.js']

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
  if (bypass(url, origin)) return 'network'
  if (request.mode === 'navigate') return 'page'
  if (isAsset(url)) return 'asset'
  if (PRECACHED.has(url.pathname + url.search)) return 'static'
  return 'network'
}

self.__kistSw = { BUILD, CACHE, bypass, route }

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
})

self.addEventListener('fetch', (event) => {
  const kind = route(event.request, self.location.origin)
  if (kind === 'network') return
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
