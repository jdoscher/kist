import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'
import { DetailPanel, DetailSheet } from './components/DetailPanel'
import { Grid } from './components/Grid'
import { JobsPanel } from './components/JobsPanel'
import { Preview } from './components/Preview'
import { Drawer, Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { UploadManager } from './components/UploadManager'
import { useIsMobile } from './mobile'
import { AppStateProvider } from './state'
import { useLayer } from './useShortcuts'
import { describeError, hasDrafts } from './writes'

function TokenGate({ onDone, overlay = false }: { onDone: () => void; overlay?: boolean }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  // over the app, the token screen is a modal layer: no grid shortcuts
  useLayer('modal', overlay)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setToken(value.trim())
    try {
      await api.stats() // needs the token (/api/health doesn't, so it can't check one)
      onDone()
    } catch (err) {
      setError(
        (err as { status?: number })?.status === 401
          ? 'That token was rejected. Check KIST_TOKEN in your .env.'
          : `Couldn't check it: ${describeError(err)}.`,
      )
    }
  }

  return (
    <div className={`token-gate${overlay ? ' overlay' : ''}`}>
      <form onSubmit={submit}>
        <h1>Kist</h1>
        <p>
          {overlay
            ? 'The token stopped working. Enter it again to carry on: your uploads and unsaved edits are still here.'
            : 'Enter the API token (KIST_TOKEN) to unlock the library.'}
        </p>
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="API token"
        />
        <button type="submit" disabled={!value.trim()}>
          Unlock
        </button>
        {error && <div className="error">{error}</div>}
      </form>
    </div>
  )
}

/** The server stopped answering (restart, network): say so instead of
 * leaving a frozen grid. Clears itself when the server is back. */
function ServerBanner() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: (q) => (q.state.status === 'error' ? 5_000 : 15_000),
    retry: false,
  })
  if (!health.isError || (health.error as { status?: number })?.status === 401) return null
  return <div className="server-banner">Can't reach the Kist server. Retrying…</div>
}

export default function App() {
  // /api/health needs no token, so waiting on it before loading data only
  // cost a round trip: start straight away, and a 401 from any request
  // brings back the token screen
  const [authed, setAuthed] = useState<boolean>(() => {
    const token = getToken()
    if (token) setToken(token) // refresh the media cookie
    return !!token
  })
  // once the app has been shown, a lost token puts the token screen OVER it
  // instead of replacing it, so the upload queue and typed text survive
  const [shown, setShown] = useState(authed)
  const qc = useQueryClient()
  const mobile = useIsMobile()

  const recheck = useCallback(() => {
    setAuthed(true)
    setShown(true)
    qc.invalidateQueries() // refetch what failed with the old token
  }, [qc])

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false))
  }, [])

  // closing the tab with text that never reached the server: ask first
  useEffect(() => {
    const onUnload = (e: BeforeUnloadEvent) => {
      if (hasDrafts()) e.preventDefault()
    }
    window.addEventListener('beforeunload', onUnload)
    return () => window.removeEventListener('beforeunload', onUnload)
  }, [])

  if (!authed && !shown) return <TokenGate onDone={recheck} />

  return (
    <AppStateProvider mobile={mobile}>
      <UploadManager>
        <div className={`app-shell${mobile ? ' mobile' : ''}`}>
          <ServerBanner />
          <TopBar mobile={mobile} />
          <div className="app-body">
            {mobile ? <Drawer /> : <Sidebar />}
            <Grid mobile={mobile} />
            {mobile ? <DetailSheet /> : <DetailPanel />}
          </div>
          <Preview />
          <JobsPanel />
          {!authed && <TokenGate onDone={recheck} overlay />}
        </div>
      </UploadManager>
    </AppStateProvider>
  )
}
