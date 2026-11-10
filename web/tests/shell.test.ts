// The app shell: service worker rules, offline, badge, install hint
// (W2.2–W2.5). Run with `npm test`.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import {
  applyBadge,
  createStore,
  isIosSafari,
  isLocalhost,
  isOffline,
  offlineText,
  onReconnect,
  reportNetwork,
  setBrowserOnline,
  shouldRegisterSW,
  shouldShowInstallHint,
  unfiledCount,
  type NetState,
} from '../src/shell.ts'
import { buildVersion, injectBuild, precacheList } from '../src/swBuild.ts'

test('the worker registers only in production, on https or this machine', () => {
  const env = { prod: true, protocol: 'https:', hostname: 'kist.example.com', supported: true }
  assert.equal(shouldRegisterSW(env), true)
  assert.equal(shouldRegisterSW({ ...env, prod: false }), false)
  assert.equal(shouldRegisterSW({ ...env, supported: false }), false)
  assert.equal(shouldRegisterSW({ ...env, protocol: 'http:' }), false)
  assert.equal(shouldRegisterSW({ ...env, protocol: 'http:', hostname: 'localhost' }), true)
  assert.equal(shouldRegisterSW({ ...env, protocol: 'http:', hostname: '127.0.0.1' }), true)
  assert.equal(shouldRegisterSW({ ...env, protocol: 'http:', hostname: '192.168.1.68' }), false)
  assert.ok(isLocalhost('[::1]') && isLocalhost('kist.localhost') && !isLocalhost('localhost.example.com'))
})

test('back online after an outage calls the reconnect hook once (failed queries refetch)', () => {
  const store = createStore<NetState>({ browserOnline: true, networkFailed: false })
  let calls = 0
  const off = onReconnect(() => calls++, store)
  reportNetwork(true, store) // online and answering: nothing to do
  assert.equal(calls, 0)
  reportNetwork(false, store) // the server stopped answering
  reportNetwork(false, store)
  assert.equal(calls, 0)
  reportNetwork(true, store) // it answers again: the browser never went offline
  assert.equal(calls, 1)
  setBrowserOnline(false, store)
  setBrowserOnline(true, store)
  assert.equal(calls, 2)
  off()
  reportNetwork(false, store)
  reportNetwork(true, store)
  assert.equal(calls, 2)
})

test('offline: browser offline, or a request got no answer; any answer clears it', () => {
  const store = createStore<NetState>({ browserOnline: true, networkFailed: false })
  assert.equal(isOffline(store.get()), false)
  reportNetwork(false, store)
  assert.equal(isOffline(store.get()), true)
  reportNetwork(true, store)
  assert.equal(isOffline(store.get()), false)
  setBrowserOnline(false, store)
  assert.equal(isOffline(store.get()), true)
  reportNetwork(false, store)
  setBrowserOnline(true, store) // reconnect: the next request decides
  assert.equal(isOffline(store.get()), false)
  assert.equal(offlineText(store.get()), '')
  assert.match(offlineText({ browserOnline: false, networkFailed: false }), /^Offline\. /)
  assert.match(offlineText({ browserOnline: true, networkFailed: true }), /^Offline: Kist’s server/)
})

test('store notifies only on change', () => {
  const s = createStore(1)
  let n = 0
  const off = s.subscribe(() => n++)
  s.set(1)
  s.set(2)
  off()
  s.set(3)
  assert.equal(n, 1)
})

test('unfiled count: stats.unfiled when the server has it, else the view total', () => {
  assert.equal(unfiledCount({ unfiled: 7 }, 99), 7)
  assert.equal(unfiledCount({ unfiled: 0 }, 99), 0)
  assert.equal(unfiledCount({}, 12), 12)
  assert.equal(unfiledCount(undefined, null), null)
})

test('badge: set above 0, cleared at 0, feature-detected, failures ignored', async () => {
  const calls: string[] = []
  const nav = {
    setAppBadge: async (n?: number) => void calls.push(`set ${n}`),
    clearAppBadge: async () => void calls.push('clear'),
  }
  assert.equal(await applyBadge(nav, 5), 'set')
  assert.equal(await applyBadge(nav, 0), 'cleared')
  assert.equal(await applyBadge(nav, null), 'skipped')
  assert.deepEqual(calls, ['set 5', 'clear'])
  assert.equal(await applyBadge({}, 3), 'unsupported')
  assert.equal(await applyBadge({ setAppBadge: () => Promise.reject(new Error('no')) }, 3), 'failed')
})

const IPHONE_SAFARI =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1'
const IPHONE_CHROME =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0 Mobile/15E148 Safari/604.1'
const IPAD_DESKTOP_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15'
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36'
const MAC_CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
const IPHONE_WEBVIEW = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'

test('install hint: mobile Safari only, not installed, not dismissed', () => {
  assert.equal(isIosSafari(IPHONE_SAFARI), true)
  assert.equal(isIosSafari(IPHONE_CHROME), false)
  assert.equal(isIosSafari(ANDROID), false)
  assert.equal(isIosSafari(IPAD_DESKTOP_UA, 5), true) // iPadOS asks for the desktop site
  assert.equal(isIosSafari(IPAD_DESKTOP_UA, 0), false) // a Mac
  assert.equal(isIosSafari(MAC_CHROME, 5), false) // Chrome on a touch Mac (or emulating touch)
  assert.equal(isIosSafari(IPHONE_WEBVIEW), false) // an app's web view
  const env = { ua: IPHONE_SAFARI, maxTouchPoints: 5, standalone: false, dismissed: false }
  assert.equal(shouldShowInstallHint(env), true)
  assert.equal(shouldShowInstallHint({ ...env, standalone: true }), false)
  assert.equal(shouldShowInstallHint({ ...env, dismissed: true }), false)
})

// ---- the worker itself (public/sw.js), run in a sandbox ----

const INDEX = `<script src="/theme-init.js?v=1"></script>
<link rel="icon" href="/favicon.svg?v=4" /><link rel="manifest" href="/manifest.webmanifest?v=1" />
<script type="module" crossorigin src="/assets/index-AAA.js"></script>
<link rel="stylesheet" href="/assets/index-BBB.css"><a href="//elsewhere.example/x">`

test('precache list: the page, the bundle (no .woff or maps) and the root files index.html links', () => {
  const list = precacheList(
    ['assets/index-AAA.js', 'assets/index-BBB.css', 'assets/inter-latin.woff2', 'assets/inter-latin.woff', 'assets/index-AAA.js.map', 'index.html'],
    INDEX,
  )
  assert.deepEqual(list, [
    '/',
    '/assets/index-AAA.js',
    '/assets/index-BBB.css',
    '/assets/inter-latin.woff2',
    '/favicon.svg?v=4',
    '/manifest.webmanifest?v=1',
    '/theme-init.js?v=1',
  ])
})

test('build version: same build, same version; any change, a new one', () => {
  const a = buildVersion(['/', '/assets/a.js'], INDEX)
  assert.equal(a, buildVersion(['/', '/assets/a.js'], INDEX))
  assert.notEqual(a, buildVersion(['/', '/assets/b.js'], INDEX))
  assert.notEqual(a, buildVersion(['/', '/assets/a.js'], INDEX, 'icon bytes'))
  assert.match(a, /^[0-9a-f]{12}$/)
})

type Listener = (e: unknown) => void

function loadWorker(build?: { version: string; assets: string[] }) {
  let src = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8')
  if (build) src = injectBuild(src, build)
  const listeners: Record<string, Listener> = {}
  const self: Record<string, unknown> = {
    location: { origin: 'https://ki.example' },
    addEventListener: (type: string, fn: Listener) => (listeners[type] = fn),
    skipWaiting: () => {
      self.skipped = true
    },
    clients: { claim: async () => undefined },
  }
  vm.runInNewContext(src, { self, URL, Request: class {}, caches: {}, fetch: () => undefined, Response: {} })
  return { sw: self.__kistSw as { CACHE: string; route: (r: unknown, o: string) => string }, listeners, self }
}

const req = (path: string, mode = 'no-cors', method = 'GET') => ({ url: `https://ki.example${path}`, mode, method })

test('the worker never handles the API, MCP, share pages, item files or other sites', () => {
  const { sw } = loadWorker({ version: 'v1', assets: ['/', '/assets/index-AAA.js', '/favicon.svg?v=4'] })
  const o = 'https://ki.example'
  for (const p of [
    '/api/items?limit=5',
    '/api/items/01H/file',
    '/api/items/01H/thumb?size=512',
    '/api',
    '/mcp',
    '/mcp/messages',
    '/s/abc123',
    '/s',
    '/docs',
    '/openapi.json',
    '/sw.js',
  ]) {
    assert.equal(sw.route(req(p), o), 'network', p)
    assert.equal(sw.route(req(p, 'navigate'), o), 'network', `${p} (navigation)`)
  }
  assert.equal(sw.route({ url: 'https://other.example/assets/x.js', mode: 'cors', method: 'GET' }, o), 'network')
  assert.equal(sw.route(req('/assets/index-AAA.js', 'cors', 'POST'), o), 'network')
})

test('the worker: pages network-first, bundle files cache-first, listed root files from the cache', () => {
  const { sw } = loadWorker({ version: 'v1', assets: ['/', '/assets/index-AAA.js', '/favicon.svg?v=4'] })
  const o = 'https://ki.example'
  assert.equal(sw.route(req('/', 'navigate'), o), 'page')
  assert.equal(sw.route(req('/folder/01H', 'navigate'), o), 'page')
  assert.equal(sw.route(req('/add?url=x', 'navigate'), o), 'page')
  assert.equal(sw.route(req('/assets/index-AAA.js', 'cors'), o), 'asset')
  assert.equal(sw.route(req('/assets/lazy-CCC.js', 'cors'), o), 'asset')
  assert.equal(sw.route(req('/favicon.svg?v=4'), o), 'static')
  assert.equal(sw.route(req('/favicon.svg?v=5'), o), 'network')
  assert.equal(sw.route(req('/robots.txt'), o), 'network')
})

test('the worker: one cache per build, waits for Reload (SKIP_WAITING)', () => {
  const { sw, listeners, self } = loadWorker({ version: 'abc123', assets: ['/'] })
  assert.equal(sw.CACHE, 'kist-shell-abc123')
  assert.ok(listeners.install && listeners.activate && listeners.fetch && listeners.message)
  listeners.message({ data: { type: 'other' } })
  assert.equal(self.skipped, undefined)
  listeners.message({ data: { type: 'SKIP_WAITING' } })
  assert.equal(self.skipped, true)
  // the unbuilt source still parses (dev version, nothing listed)
  assert.equal(loadWorker().sw.CACHE, 'kist-shell-dev')
})
