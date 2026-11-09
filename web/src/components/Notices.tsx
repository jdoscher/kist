// Renders notices.ts's list: one polite live region at the bottom of the
// screen. useNotices().notify({...}) from anywhere below the provider.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { addNotice, removeNotice, toNotice, type Notice, type NoticeInput } from '../notices'

interface NoticesApi {
  /** show a notice; returns its id */
  notify: (input: NoticeInput) => number
  /** remove a notice by id, or every notice with a key */
  dismiss: (idOrKey: number | string) => void
}

const Ctx = createContext<NoticesApi | null>(null)

export function useNotices(): NoticesApi {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useNotices outside NoticesProvider')
  return ctx
}

let nextId = 1

export function NoticesProvider({ children }: { children: ReactNode }) {
  const [list, setList] = useState<Notice[]>([])
  const dismiss = useCallback((idOrKey: number | string) => setList((l) => removeNotice(l, idOrKey)), [])
  const notify = useCallback((input: NoticeInput) => {
    const n = toNotice(input, nextId++)
    setList((l) => addNotice(l, n))
    return n.id
  }, [])
  const api = useMemo(() => ({ notify, dismiss }), [notify, dismiss])
  return (
    <Ctx.Provider value={api}>
      {children}
      {/* outside every overlay's inert background (useInertBackground), so
          Undo / Keep mine stay reachable while a dialog is open */}
      <div className="notices" role="status" aria-live="polite" data-keep-live="">
        {list.map((n) => (
          <NoticeRow key={n.id} notice={n} onDismiss={() => dismiss(n.id)} />
        ))}
      </div>
    </Ctx.Provider>
  )
}

function NoticeRow({ notice, onDismiss }: { notice: Notice; onDismiss: () => void }) {
  // the timer pauses while the pointer or focus is on the notice
  const [paused, setPaused] = useState(false)
  const dismissRef = useRef(onDismiss)
  dismissRef.current = onDismiss
  useEffect(() => {
    if (notice.timeoutMs === null || paused) return
    const t = setTimeout(() => dismissRef.current(), notice.timeoutMs)
    return () => clearTimeout(t)
  }, [notice.timeoutMs, paused])
  return (
    <div
      className={`notice ${notice.tone}`}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      {/* failures interrupt (alert); everything else is read politely */}
      <span className="notice-text" role={notice.tone === 'error' ? 'alert' : undefined}>
        {notice.message}
      </span>
      {notice.actions.map((a) => (
        <button
          key={a.label}
          onClick={() => {
            onDismiss()
            a.run()
          }}
        >
          {a.label}
        </button>
      ))}
      <button className="notice-close" aria-label="Dismiss notice" onClick={onDismiss}>
        <span aria-hidden="true">✕</span>
      </button>
    </div>
  )
}
