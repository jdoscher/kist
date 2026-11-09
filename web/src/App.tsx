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

function TokenGate({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setToken(value.trim())
    try {
      await api.health()
      onDone()
    } catch {
      setError('That token was rejected. Check KIST_TOKEN in your .env.')
    }
  }

  return (
    <div className="token-gate">
      <form onSubmit={submit}>
        <h1>Kist</h1>
        <p>Enter the API token (KIST_TOKEN) to unlock the library.</p>
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
  const [authed, setAuthed] = useState<boolean | null>(null)
  const qc = useQueryClient()
  const mobile = useIsMobile()

  const recheck = useCallback(() => {
    setAuthed(true)
    qc.clear()
  }, [qc])

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false))
    const token = getToken()
    if (!token) {
      setAuthed(false)
      return
    }
    setToken(token) // refresh the media cookie
    api
      .health()
      .then(() => setAuthed(true))
      .catch((err) => setAuthed(err?.status === 401 ? false : true))
  }, [])

  if (authed === null) return <div className="boot">Loading…</div>
  if (!authed) return <TokenGate onDone={recheck} />

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
        </div>
      </UploadManager>
    </AppStateProvider>
  )
}
