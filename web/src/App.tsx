import { useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from 'react'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'
import { DetailPanel } from './components/DetailPanel'
import { DetailSheet } from './components/DetailSheet'
import { ShellStatus, useOfflineText } from './components/AppShell'
import { DialogProvider } from './components/Dialog'
import { Grid } from './components/Grid'
import { JobsPanel } from './components/JobsPanel'
import { CutoutHost } from './components/cutouts/Cutouts' // F7
import { NoticesProvider } from './components/Notices'
import { Preview } from './components/Preview'
import { RecentChanges } from './components/RecentChanges'
import { Drawer, Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { UploadManager } from './components/UploadManager'
import { useIsMobile } from './mobile'
import { AppStateProvider } from './state'
import { useFocusTrap, useInertBackground } from './useInert'
import { useLayer, useShortcut } from './useShortcuts'
import { cantBrowseText } from './tokens'
import { useCaps } from './data'
import { Mark, Wordmark } from './components/Brand'
import { useAppState } from './state'
import { bannerText, describeError, hasDrafts } from './writes'
import { CreativeProvider } from './components/m3/Creative'
import { M45Provider } from './components/m45/M45'
import { inLibraryFrame } from './url'

// Screens past the library load on first use, so the first load is the
// library alone (Settings, the boards list, a board, triage, Organise;
// compare, the command palette and the dialogs load in Creative.tsx)
const SettingsScreen = lazy(() => import('./components/Settings').then((m) => ({ default: m.SettingsScreen })))
const BoardsScreen = lazy(() => import('./components/m3/BoardsScreen').then((m) => ({ default: m.BoardsScreen })))
const BoardEditor = lazy(() => import('./components/m3/board/BoardEditor'))
const Triage = lazy(() => import('./components/m3/Triage'))
// Milestones 4 and 5: the timeline, the map (MapLibre loads with it), the
// bookmarklet's /add card and its /bookmarklet generator
const TimelineScreen = lazy(() => import('./components/m45/TimelineScreen'))
const MapScreen = lazy(() => import('./components/m45/MapScreen'))
const AddPage = lazy(() => import('./components/m45/AddPage'))
const BookmarkletPage = lazy(() => import('./components/m45/BookmarkletPage'))

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
      // /api/me answers any valid token (/api/health needs none, so it can't
      // check one; /api/stats is 404 for a folder-limited reader)
      let me = null
      try {
        me = await api.me()
      } catch (err) {
        // an older server without /api/me: fall back to stats
        if ((err as { status?: number })?.status !== 404) throw err
        await api.stats()
      }
      const why = me ? cantBrowseText(me) : null
      if (why) {
        setToken('') // don't keep a token that can't open the library
        setError(why)
        return
      }
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
        <h1 id={`${id}-title`} className="gate-brand">
          <Mark className="gate-mark" />
          <Wordmark className="gate-wordmark" />
        </h1>
        <p className="tagline">Keep what catches your eye.</p>
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
        <button type="submit" className="btn-primary" disabled={!value.trim()}>
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

/** ⌘Z / Ctrl+Z (outside text fields, which keep their own undo) opens
 * Recent changes with focus on the newest Undo. A board has its own undo
 * (keymap.ts: one binding per key on each screen). */
function UndoShortcut() {
  const { setRecentOpen, screen } = useAppState()
  const { canEdit } = useCaps()
  useShortcut(
    'grid',
    (e) => {
      if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== 'z') return false
      e.preventDefault()
      setRecentOpen(true)
      return true
    },
    canEdit && screen?.kind !== 'board',
  )
  return null
}

// Organise (duplicates, stacks, collections, people) loads when first opened
const OrganiseScreen = lazy(() => import('./components/Organise').then((m) => ({ default: m.OrganiseScreen })))

/** The library (top bar, sidebar, grid, info panel), Settings or Organise. */
function Screens({ mobile }: { mobile: boolean }) {
  const { settings, organise, screen } = useAppState()
  if (settings)
    return (
      <Suspense fallback={<main className="grid-area center" role="status">Loading…</main>}>
        <SettingsScreen />
      </Suspense>
    )
  if (organise)
    return (
      <Suspense fallback={<main className="grid-area center" role="status">Loading…</main>}>
        <OrganiseScreen />
      </Suspense>
    )
  // the board editor and triage take the whole window (focus); the boards
  // list sits in the library's frame, in place of the grid
  if (screen && !inLibraryFrame(screen))
    return (
      <Suspense fallback={<main className="center" role="status">Loading…</main>}>
        {screen.kind === 'add' && <AddPage search={screen.search} />}
        {screen.kind === 'board' && <BoardEditor key={screen.id} id={screen.id} />}
        {screen.kind === 'triage' && <Triage key={`${screen.view}:${screen.folder ?? ''}`} screen={screen} />}
      </Suspense>
    )
  return (
    <>
      <TopBar mobile={mobile} />
      <div className="app-body">
        {mobile ? <Drawer /> : <DesktopSidebar />}
        {inLibraryFrame(screen) ? (
          <Suspense fallback={<main className="grid-area center" role="status">Loading…</main>}>
            {screen?.kind === 'boards' && <BoardsScreen trash={!!screen.trash} />}
            {screen?.kind === 'timeline' && <TimelineScreen group={screen.group} />}
            {screen?.kind === 'map' && <MapScreen />}
          </Suspense>
        ) : (
          <>
            <Grid mobile={mobile} />
            {mobile ? <DetailSheet /> : <DetailPanel />}
          </>
        )}
      </div>
    </>
  )
}

/** Gallery density tucks the sidebar away until Panels is pressed. */
function DesktopSidebar() {
  const { density, chromeShown } = useAppState()
  return density === 'gallery' && !chromeShown ? null : <Sidebar />
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
  // no answer at all (no connection, server gone): the Offline banner
  // (shell.ts); an answer with an error: the database line
  const offline = useOfflineText()
  const text = offline || (health.isError && status !== 401 && status !== 0 ? bannerText(status) : '')
  // the live region is always there, so the message is read out when it appears
  return (
    <div className="server-banner-wrap" role="status" aria-live="polite">
      {text && <div className={`server-banner${offline ? ' offline-banner' : ''}`}>{text}</div>}
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

  // the bookmarklet generator needs no token (it never holds one)
  if (window.location.pathname === '/bookmarklet')
    return (
      <Suspense fallback={<main className="center" role="status">Loading…</main>}>
        <BookmarkletPage />
      </Suspense>
    )

  if (!authed && !shown) return <TokenGate onDone={recheck} />

  return (
    <NoticesProvider>
      <DialogProvider>
        <AppStateProvider mobile={mobile}>
          <UploadManager>
            <CreativeProvider>
              <M45Provider>
              <div className={`app-shell${mobile ? ' mobile' : ''}`}>
                <ServerBanner />
                <ShellStatus />
                <Screens mobile={mobile} />
                <Preview />
                <JobsPanel />
                <CutoutHost />
                <RecentChanges />
                <UndoShortcut />
                {!authed && <TokenGate onDone={recheck} overlay />}
              </div>
              </M45Provider>
            </CreativeProvider>
          </UploadManager>
        </AppStateProvider>
      </DialogProvider>
    </NoticesProvider>
  )
}
