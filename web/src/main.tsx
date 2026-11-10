import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { installErrorReporting } from './reportError'
import { installShortcuts } from './shortcuts'
import '@fontsource-variable/inter/wght.css'
import '@fontsource/instrument-serif/400.css'
import '@fontsource-variable/jetbrains-mono/wght.css'
import './themes.css'
import './styles.css'
import './m2.css'
import './creative.css'
import { readPrefs } from './settings'
import { applyAppearance, watchSystemTheme } from './theme'

installErrorReporting(window)
installShortcuts(window)
// theme-init.js already painted the stored appearance; keep it in step
// with the OS while the theme is System
applyAppearance(readPrefs().theme, readPrefs().density)
watchSystemTheme(() => readPrefs())

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
