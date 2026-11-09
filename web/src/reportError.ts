// Browser crash reporting (tests/reportError.test.ts). The ErrorBoundary and
// the window 'error' / 'unhandledrejection' handlers send what broke to
// POST /api/client-errors (Kist 1.5 contract item 4), fire-and-forget:
// a failed report is ignored, identical messages are sent once a minute,
// and 401s (signed out, not a bug) are never reported.

import { ApiError, getToken } from './api.ts'

export type ErrorKind = 'render' | 'error' | 'unhandledrejection'

export interface ClientErrorReport {
  message: string
  stack?: string
  url?: string
  user_agent?: string
  kind: ErrorKind
}

const MAX_MESSAGE = 2000
const MAX_STACK = 10000
export const DEDUPE_MS = 60_000

// browser noise that isn't a Kist bug
const IGNORED = [/ResizeObserver loop/i]

/** Build a report from whatever was thrown; null when it shouldn't be sent. */
export function toReport(kind: ErrorKind, thrown: unknown, extraStack?: string): ClientErrorReport | null {
  if (thrown instanceof ApiError && thrown.status === 401) return null
  if ((thrown as { status?: unknown } | null)?.status === 401) return null
  let message: string
  let stack: string | undefined
  if (thrown instanceof Error) {
    message = `${thrown.name}: ${thrown.message}`
    stack = thrown.stack
  } else if (typeof thrown === 'string') {
    message = thrown
  } else {
    try {
      message = JSON.stringify(thrown) ?? String(thrown)
    } catch {
      message = String(thrown)
    }
  }
  if (!message || IGNORED.some((re) => re.test(message))) return null
  if (extraStack) stack = stack ? `${stack}\n\nComponent stack:${extraStack}` : extraStack
  return {
    kind,
    message: message.slice(0, MAX_MESSAGE),
    ...(stack ? { stack: stack.slice(0, MAX_STACK) } : {}),
  }
}

export function createReporter(opts: {
  send: (body: ClientErrorReport) => Promise<unknown>
  now?: () => number
  token?: () => string
  context?: () => { url?: string; user_agent?: string }
}) {
  const seen = new Map<string, number>()
  const now = opts.now ?? Date.now
  return (report: ClientErrorReport | null): boolean => {
    if (!report) return false
    if (opts.token && !opts.token()) return false // signed out: the server would 401
    const t = now()
    for (const [m, at] of seen) if (t - at >= DEDUPE_MS) seen.delete(m)
    const key = `${report.kind}:${report.message}`
    if (seen.has(key)) return false
    seen.set(key, t)
    try {
      void opts.send({ ...opts.context?.(), ...report }).catch(() => undefined)
    } catch {
      /* never let reporting throw */
    }
    return true
  }
}

function sendToServer(body: ClientErrorReport): Promise<unknown> {
  // plain fetch, not api.ts's request(): a 401 here must not bring up the
  // token screen, and nothing waits for the answer
  return fetch('/api/client-errors', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify(body),
    keepalive: true,
  })
}

export const reportClientError = createReporter({
  send: sendToServer,
  token: getToken,
  context: () => ({ url: window.location.href, user_agent: navigator.userAgent }),
})

/** Report uncaught errors and unhandled promise rejections. */
export function installErrorReporting(win: Window) {
  win.addEventListener('error', (e: ErrorEvent) => {
    reportClientError(toReport('error', e.error ?? e.message))
  })
  win.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => {
    reportClientError(toReport('unhandledrejection', e.reason))
  })
}
