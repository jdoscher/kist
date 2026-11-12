// The app shell's status pieces (Kist 2.0 Milestone 6, W2.2–W2.5): the
// "Kist was updated" notice, the Offline state, the icon badge with the
// Unfiled count, and the one-time iPhone "Add to Home Screen" hint. Logic
// in shell.ts.

import { useQuery } from '@tanstack/react-query'
import { Share, X } from 'lucide-react'
import { useEffect, useState, useSyncExternalStore } from 'react'
import { emptyQuery, itemsParams, request, type ItemPage, type Query } from '../api'
import { useCaps, useStats } from '../data'
import { unfiledQuery } from '../query'
import { markOnce, onceSeen } from '../settings'
import {
  applyBadge,
  INSTALL_HINT_ONCE,
  netState,
  offlineText,
  requestReload,
  shouldShowInstallHint,
  swUpdate,
  unfiledCount,
  UPDATE_TEXT,
  type BadgeNavigator,
} from '../shell'
import { useNotices } from './Notices'

/** '' when online, else what the banner says (shell.ts offlineText). */
export function useOfflineText(): string {
  return useSyncExternalStore(netState.subscribe, () => offlineText(netState.get()))
}

/** A new version is installed and waiting: say so, with Reload. */
function UpdateNotice() {
  const waiting = useSyncExternalStore(swUpdate.subscribe, swUpdate.get)
  const { notify } = useNotices()
  useEffect(() => {
    if (!waiting) return
    notify({
      key: 'sw-update',
      message: UPDATE_TEXT,
      action: { label: 'Reload', run: () => requestReload(waiting) },
      timeoutMs: null,
    })
  }, [waiting, notify])
  return null
}

const badgeNav = (typeof navigator === 'undefined' ? {} : navigator) as BadgeNavigator
const BADGE_SUPPORTED = typeof badgeNav.setAppBadge === 'function'

/**
 * The Unfiled count on the app icon (installed web app): GET /api/stats'
 * `unfiled` when the server has it, else the Unfiled view's total. Both
 * queries sit under ['stats'], which every edit (useApplyItems), undo and
 * triage step marks stale, so filing something updates the badge; coming
 * back to the app re-reads it.
 */
function AppBadge() {
  const { restricted, ready } = useCaps()
  const stats = useStats()
  const needView = BADGE_SUPPORTED && ready && !restricted && !!stats.data && typeof stats.data.unfiled !== 'number'
  const view = useQuery({
    queryKey: ['stats', 'unfiled-view'],
    queryFn: () =>
      request<ItemPage>(`/api/items?${itemsParams({ ...emptyQuery, ...unfiledQuery } as Query, undefined, 1)}`),
    enabled: needView,
    staleTime: 30_000,
  })
  const count = BADGE_SUPPORTED && !restricted ? unfiledCount(stats.data, view.data?.total) : null

  useEffect(() => {
    void applyBadge(badgeNav, count)
  }, [count])

  // back to the app (focus, tab shown): read the count again
  const { refetch: refetchStats } = stats
  const { refetch: refetchView } = view
  useEffect(() => {
    if (!BADGE_SUPPORTED) return
    const again = () => {
      if (document.visibilityState !== 'visible') return
      void refetchStats()
      if (needView) void refetchView()
    }
    window.addEventListener('focus', again)
    document.addEventListener('visibilitychange', again)
    return () => {
      window.removeEventListener('focus', again)
      document.removeEventListener('visibilitychange', again)
    }
  }, [refetchStats, refetchView, needView])
  return null
}

function standalone(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean }
  return nav.standalone === true || window.matchMedia?.('(display-mode: standalone)').matches === true
}

/** Mobile Safari, not opened from the Home Screen: how to install Kist,
 * once (dismissing it is remembered in this browser). */
function InstallHint() {
  const [show, setShow] = useState(() =>
    shouldShowInstallHint({
      ua: navigator.userAgent,
      maxTouchPoints: navigator.maxTouchPoints ?? 0,
      standalone: standalone(),
      dismissed: onceSeen(INSTALL_HINT_ONCE),
    }),
  )
  if (!show) return null
  const dismiss = () => {
    markOnce(INSTALL_HINT_ONCE)
    setShow(false)
  }
  return (
    <section className="install-hint" aria-label="Install Kist">
      <p>
        Add Kist to your Home Screen: Share{' '}
        <Share size={14} aria-label="(the square with an arrow)" role="img" /> → Add to Home Screen
      </p>
      <button className="icon-btn" onClick={dismiss} aria-label="Dismiss the Home Screen hint">
        <X size={16} aria-hidden="true" />
      </button>
    </section>
  )
}

export function ShellStatus() {
  return (
    <>
      <UpdateNotice />
      <AppBadge />
      <InstallHint />
    </>
  )
}
