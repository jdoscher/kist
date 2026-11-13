// Present (Kist 2.0 F2): the presenter view, a second window
// (/present/presenter?ch=<show>). It follows the show window over a
// BroadcastChannel: the current slide, the next one, the notes (the item's
// notes, then its caption), the time since the start, the clock and the
// slide timer. Its buttons and arrow keys drive the show; it can also show
// the phone remote's QR code, so the code needn't go on the shared screen.

import { Ban, Check, ChevronLeft, ChevronRight, Smartphone, Square } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { channelName, formatClock, type PresentState, type PresenterMessage, type RemoteCommand, type Slide } from '../../present'
import { QrCode } from './PairPanel'
import { remoteUrl } from '../../present'
import '../../present.css'

function Thumbs({ slide, big }: { slide: Slide | null; big?: boolean }) {
  if (!slide) return <div className="present-pv-end">{big ? '' : 'End of the show'}</div>
  return (
    <div className={`present-pv-thumbs${slide.kind === 'compare' ? ' compare' : ''}`}>
      {slide.items.map((it) => (
        <figure key={it.id}>
          {it.thumb ? <img src={it.thumb} alt={it.title || it.filename} /> : <div className="present-empty">No picture</div>}
          <figcaption>
            {[it.title, it.credit].filter(Boolean).join(' — ') || it.filename}
            {it.flag && <span className="present-flag">{it.flag === 'pick' ? 'Picked' : 'Rejected'}</span>}
          </figcaption>
        </figure>
      ))}
    </div>
  )
}

export default function PresenterPage() {
  const key = useMemo(() => new URLSearchParams(window.location.search).get('ch') ?? '', [])
  const [state, setState] = useState<PresentState | null>(null)
  const [remote, setRemote] = useState<{ path: string | null; connected: boolean }>({ path: null, connected: false })
  const [showQr, setShowQr] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const ch = useMemo(() => (key && typeof BroadcastChannel === 'function' ? new BroadcastChannel(channelName(key)) : null), [key])

  useEffect(() => {
    if (!ch) return
    ch.onmessage = (e: MessageEvent) => {
      const m = e.data as PresenterMessage | { type: 'remote'; path: string | null; connected: boolean }
      if (m.type === 'state') setState(m.state)
      else if (m.type === 'remote') setRemote({ path: m.path, connected: m.connected })
    }
    ch.postMessage({ type: 'hello' } satisfies PresenterMessage)
    return () => ch.close()
  }, [ch])
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(t)
  }, [])
  useEffect(() => {
    document.title = state ? `Presenter: ${state.name}` : 'Presenter view'
  }, [state])

  const send = (cmd: RemoteCommand, index?: number) => ch?.postMessage({ type: 'cmd', cmd, index } satisfies PresenterMessage)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (['ArrowRight', 'ArrowDown', 'PageDown', ' '].includes(e.key)) send('next')
      else if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(e.key)) send('prev')
      else if (e.key === 'Home') send('first')
      else if (e.key === 'End') send('last')
      else if (e.key.toLowerCase() === 'b') send('blank')
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!ch) return <main className="present-pv present-pv-wait center">Open the presenter view from a running show (V).</main>
  if (!state)
    return (
      <main className="present-pv present-pv-wait center" role="status">
        Waiting for the show… Keep the show window open.
      </main>
    )
  const left = state.slideEndsAt ? state.slideEndsAt - now : null
  const compare = state.slide?.kind === 'compare'
  return (
    <main className="present-pv">
      <header className="present-pv-bar">
        <span className="present-name">{state.name}</span>
        <span className="present-count">
          Slide {state.index + 1} of {state.count}
        </span>
        <span className="present-pv-clock" title="Time since the show started">
          {formatClock(now - state.startedAt)}
        </span>
        {left !== null && <span className="present-pv-clock" title="This slide's timer">{state.paused ? 'Paused' : formatClock(left + 999)}</span>}
        <span className="present-pv-clock" title="Time of day">
          {new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
        {state.blank && <span className="present-flag">Screen blank</span>}
      </header>
      <section className="present-pv-now" aria-label="Current slide">
        <Thumbs slide={state.slide} big />
      </section>
      <aside className="present-pv-side">
        <section aria-label="Next slide">
          <h2 className="section-label">Next</h2>
          <Thumbs slide={state.next} />
        </section>
        <section aria-label="Notes" className="present-pv-notes">
          <h2 className="section-label">Notes</h2>
          {state.notes ? <p>{state.notes}</p> : <p className="present-pv-none">No notes on this slide. They come from the item’s Notes (or its caption).</p>}
        </section>
      </aside>
      <footer className="present-pv-controls">
        <button type="button" className="btn-icon" onClick={() => send('prev')} disabled={state.index === 0}>
          <ChevronLeft size={16} aria-hidden="true" /> Previous
        </button>
        <button type="button" className="btn-primary btn-icon" onClick={() => send('next')} disabled={state.index >= state.count - 1}>
          Next <ChevronRight size={16} aria-hidden="true" />
        </button>
        <button type="button" className="btn-icon" onClick={() => send('pick', 0)}>
          <Check size={16} aria-hidden="true" /> {compare ? 'Pick left' : 'Pick'}
        </button>
        {compare && (
          <button type="button" className="btn-icon" onClick={() => send('pick', 1)}>
            <Check size={16} aria-hidden="true" /> Pick right
          </button>
        )}
        <button type="button" className="btn-icon" onClick={() => send('reject', 0)}>
          <Ban size={16} aria-hidden="true" /> {compare ? 'Reject left' : 'Reject'}
        </button>
        <button type="button" className={`btn-icon${state.blank ? ' on' : ''}`} aria-pressed={state.blank} onClick={() => send('blank')}>
          <Square size={16} aria-hidden="true" /> Blank
        </button>
        <span className="grow" />
        <button
          type="button"
          className="btn-icon"
          aria-expanded={showQr}
          onClick={() => {
            if (!remote.path) ch.postMessage({ type: 'pair' })
            setShowQr((s) => !s)
          }}
        >
          <Smartphone size={16} aria-hidden="true" /> {remote.connected ? 'Phone connected' : 'Phone remote'}
        </button>
      </footer>
      {showQr && (
        <div className="present-pair float-surface present-pv-qr">
          {remote.path ? <QrCode text={remoteUrl(window.location.origin, remote.path)} label="QR code for the phone remote" /> : <p role="status">Pairing…</p>}
          <div className="present-pair-actions">
            <button type="button" onClick={() => ch.postMessage({ type: 'unpair' })} disabled={!remote.path}>
              Unpair
            </button>
            <button type="button" onClick={() => setShowQr(false)}>
              Close
            </button>
          </div>
        </div>
      )}
    </main>
  )
}
