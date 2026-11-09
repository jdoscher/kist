// The one in-app dialog. <Modal> is the shell (role=dialog, aria-modal,
// focus trapped inside, Escape and the backdrop close it, focus goes back
// where it was, keyboard shortcuts behind it stand down). useDialog()
// gives promise-based confirm() and prompt() built on it, replacing
// window.confirm/prompt/alert.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useInertBackground } from '../useInert'
import { useLayer } from '../useShortcuts'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({
  title,
  onClose,
  children,
  className = '',
  headerClose = true,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  className?: string
  /** show a ✕ in the header */
  headerClose?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useLayer('modal', true)
  // the page behind is inert: Tab, clicks and screen readers stay in here
  useInertBackground(overlayRef)

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    const box = ref.current
    // first [data-autofocus], else the first focusable, else the box
    const first =
      box?.querySelector<HTMLElement>('[data-autofocus]') ?? box?.querySelector<HTMLElement>(FOCUSABLE) ?? box
    first?.focus()
    if (first instanceof HTMLInputElement) first.select()
    return () => {
      if (before && document.contains(before)) before.focus()
    }
  }, [])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      e.preventDefault()
      closeRef.current()
      return
    }
    if (e.key !== 'Tab' || !ref.current) return
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null)
    if (items.length === 0) {
      e.preventDefault()
      return
    }
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || !ref.current.contains(active))) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && (active === last || !ref.current.contains(active))) {
      e.preventDefault()
      first.focus()
    }
  }

  return (
    <div ref={overlayRef} className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        className={`modal ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <div className="modal-head">
          <h3 id={titleId}>{title}</h3>
          {headerClose && (
            <button className="icon-btn" onClick={onClose} aria-label={`Close ${title}`}>
              <span aria-hidden="true">✕</span>
            </button>
          )}
        </div>
        {children}
      </div>
    </div>
  )
}

interface ConfirmOptions {
  title: string
  message?: string
  confirmLabel?: string
  danger?: boolean
}

interface PromptOptions {
  title: string
  label?: string
  initial?: string
  placeholder?: string
  confirmLabel?: string
}

interface DialogApi {
  /** resolves true when confirmed, false when cancelled */
  confirm: (o: ConfirmOptions) => Promise<boolean>
  /** resolves the entered text (trimmed, non-empty), or null when cancelled */
  prompt: (o: PromptOptions) => Promise<string | null>
}

type Request =
  | { kind: 'confirm'; o: ConfirmOptions; resolve: (v: boolean) => void }
  | { kind: 'prompt'; o: PromptOptions; resolve: (v: string | null) => void }

const Ctx = createContext<DialogApi | null>(null)

export function useDialog(): DialogApi {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useDialog outside DialogProvider')
  return ctx
}

export function DialogProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<Request[]>([])
  const confirm = useCallback(
    (o: ConfirmOptions) => new Promise<boolean>((resolve) => setQueue((q) => [...q, { kind: 'confirm', o, resolve }])),
    [],
  )
  const prompt = useCallback(
    (o: PromptOptions) =>
      new Promise<string | null>((resolve) => setQueue((q) => [...q, { kind: 'prompt', o, resolve }])),
    [],
  )
  const api = useMemo(() => ({ confirm, prompt }), [confirm, prompt])
  const current = queue[0]
  const done = () => setQueue((q) => q.slice(1))

  return (
    <Ctx.Provider value={api}>
      {children}
      {current?.kind === 'confirm' && (
        <ConfirmDialog
          key={queue.length}
          o={current.o}
          onDone={(v) => {
            current.resolve(v)
            done()
          }}
        />
      )}
      {current?.kind === 'prompt' && (
        <PromptDialog
          key={queue.length}
          o={current.o}
          onDone={(v) => {
            current.resolve(v)
            done()
          }}
        />
      )}
    </Ctx.Provider>
  )
}

function ConfirmDialog({ o, onDone }: { o: ConfirmOptions; onDone: (v: boolean) => void }) {
  return (
    <Modal title={o.title} onClose={() => onDone(false)} className="dialog" headerClose={false}>
      {o.message && <p className="dialog-message">{o.message}</p>}
      <div className="dialog-actions">
        <button onClick={() => onDone(false)}>Cancel</button>
        <button className={o.danger ? 'danger' : 'primary'} data-autofocus onClick={() => onDone(true)}>
          {o.confirmLabel ?? 'OK'}
        </button>
      </div>
    </Modal>
  )
}

function PromptDialog({ o, onDone }: { o: PromptOptions; onDone: (v: string | null) => void }) {
  const [value, setValue] = useState(o.initial ?? '')
  const inputId = useId()
  const ok = value.trim().length > 0
  return (
    <Modal title={o.title} onClose={() => onDone(null)} className="dialog" headerClose={false}>
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) onDone(value.trim())
        }}
      >
        {o.label && <label htmlFor={inputId}>{o.label}</label>}
        <input
          id={inputId}
          data-autofocus
          value={value}
          placeholder={o.placeholder}
          aria-label={o.label ? undefined : o.title}
          onChange={(e) => setValue(e.target.value)}
        />
        <div className="dialog-actions">
          <button type="button" onClick={() => onDone(null)}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={!ok}>
            {o.confirmLabel ?? 'OK'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
