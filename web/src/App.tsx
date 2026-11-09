import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'
import { DetailPanel } from './components/DetailPanel'
import { DetailSheet } from './components/DetailSheet'
import { DialogProvider } from './components/Dialog'
import { Grid } from './components/Grid'
import { JobsPanel } from './components/JobsPanel'
import { NoticesProvider } from './components/Notices'
import { Preview } from './components/Preview'
import { Drawer, Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { UploadManager } from './components/UploadManager'
import { useIsMobile } from './mobile'
import { AppStateProvider } from './state'
import { useFocusTrap, useInertBackground } from './useInert'
import { useLayer } from './useShortcuts'
import { bannerText, describeError, hasDrafts } from './writes'

function TokenGate({ onDone, overlay = false }: { onDone: () => void; overlay?: boolean }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const id = useId()
  const ref = useRef<HTMLDivElement>(null)
  // over the app, the token screen is a modal layer: no grid shortcuts,
  // and the app behind it is inert (Tab can't reach it)
  useLayer('modal', overlay)
  useInertBackground(ref, overlay)
  useFocusTrap(ref, overlay)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setToken(value.trim())
    try {
      await api.stats() // needs the token (/api/health doesn't, so it can't check one)
      onDone()
    } catch (err) {
      setError(
        (err as { status?: number })?.status === 401
          ? 'That token didn’t work. Check it and try again.'
          : `Couldn't check it: ${describeError(err)}.`,
      )
    }
  }

  return (
    <div
      ref={ref}
      className={`token-gate${overlay ? ' overlay' : ''}`}
      role={overlay ? 'dialog' : 'main'}
      aria-modal={overlay ? 'true' : undefined}
      aria-labelledby={`${id}-title`}
    >
      <form onSubmit={submit}>
        <h1 id={`${id}-title`}>Kist</h1>
        <p>
          {overlay
            ? 'The access token stopped working. Enter it again to carry on: your uploads and unsaved edits are still here.'
            : 'Enter the access token to open the library. Ask whoever set up Kist for it.'}
        </p>
        <label htmlFor={`${id}-token`}>Access token</label>
        <input
          id={`${id}-token`}
          type="password"
          autoFocus
          autoComplete="current-password"
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            setError('')
          }}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <button type="submit" className="primary" disabled={!value.trim()}>
          Unlock
        </button>
        <div id={`${id}-error`} className="error" role="alert">
          {error}
        </div>
        <details className="token-help">
          <summary>Where do I find it?</summary>
          <p>
            It’s the <code>KIST_TOKEN</code> value in the <code>.env</code> file on the computer that runs Kist. The
            person who installed Kist can read it there.
          </p>
        </details>
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
  const status = (health.error as { status?: number } | null)?.status
  const text = health.isError && status !== 401 ? bannerText(status) : ''
  // the live region is always there, so the message is read out when it appears
  return (
    <div className="server-banner-wrap" role="status" aria-live="polite">
      {text && <div className="server-banner">{text}</div>}
    </div>
  )
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
    <NoticesProvider>
      <DialogProvider>
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
      </DialogProvider>
    </NoticesProvider>
  )
}
