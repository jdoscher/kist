import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { installErrorReporting } from './reportError'
import { installShortcuts } from './shortcuts'
import '@fontsource-variable/inter/wght.css'
// the real italic: with font-synthesis: none the browser never fakes one
import '@fontsource-variable/inter/wght-italic.css'
import '@fontsource/instrument-serif/400.css'
import '@fontsource-variable/jetbrains-mono/wght.css'
import './themes.css'
import './styles.css'
import './m2.css'
import './creative.css'
import './m45.css'
import './phone.css'
import './kinds.css' // F1 utility collections
import { readPrefs } from './settings'
import { onReconnect, reloadRequested, setBrowserOnline, shouldRegisterSW, swUpdate } from './shell'
import { applyAppearance, watchSystemTheme } from './theme'

installErrorReporting(window)
installShortcuts(window)
// theme-init.js already painted the stored appearance; keep it in step
// with the OS while the theme is System
applyAppearance(readPrefs().theme, readPrefs().density)
watchSystemTheme(() => readPrefs())
watchOnline()
registerServiceWorker()

/** The browser's own online/offline events feed the Offline banner. */
function watchOnline() {
  window.addEventListener('online', () => setBrowserOnline(true))
  window.addEventListener('offline', () => setBrowserOnline(false))
}

/**
 * The service worker (public/sw.js) keeps the app shell for offline use.
 * Production builds only, on https or localhost (shell.ts). A new version
 * installs in the background and waits; AppShell shows "Kist was updated"
 * with Reload, which tells it to take over, and the page reloads once it
 * has (controllerchange).
 */
function registerServiceWorker() {
  const env = {
    prod: import.meta.env.PROD,
    protocol: location.protocol,
    hostname: location.hostname,
    supported: 'serviceWorker' in navigator,
  }
  if (!shouldRegisterSW(env)) return
  const sw = navigator.serviceWorker
  const hadController = !!sw.controller
  // a page that had no worker (first visit) gets one without a reload;
  // only a Reload pressed on the notice reloads. Another tab's Reload
  // swaps this tab's worker too: offer a plain reload here.
  sw.addEventListener('controllerchange', () => {
    if (reloadRequested()) location.reload()
    else if (hadController) swUpdate.set({ postMessage: () => location.reload() })
  })
  window.addEventListener('load', () => {
    sw.register('/sw.js', { scope: '/' }).then(
      (reg) => {
        const offer = () => {
          // only an update: a first install has no controller to replace
          if (reg.waiting && sw.controller) swUpdate.set(reg.waiting)
        }
        offer()
        reg.addEventListener('updatefound', () => {
          const next = reg.installing
          next?.addEventListener('statechange', () => {
            if (next.state === 'installed') offer()
          })
        })
        // a tab left open for days still learns about a redeploy
        const check = () => {
          if (document.visibilityState === 'visible') void reg.update().catch(() => undefined)
        }
        document.addEventListener('visibilitychange', check)
        window.setInterval(check, 60 * 60_000)
      },
      () => undefined, // no worker: Kist works the same, just not offline
    )
  })
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
})

// the server answers again after an outage: load what failed meanwhile
// (the grid's "Failed to load", the counts) instead of leaving it until
// the next navigation (shell.ts onReconnect)
onReconnect(() => void queryClient.refetchQueries({ type: 'active', predicate: (q) => q.state.status === 'error' }))

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
