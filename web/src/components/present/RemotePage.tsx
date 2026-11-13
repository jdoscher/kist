// Present (Kist 2.0 F2): the phone remote, /present/remote/<id>#<token>.
// No Kist token and no library access: it long-polls the show window's
// state through the relay (/present-relay/<id>/state) and sends next /
// previous / pick / reject / blank. Thumbnails come through the relay too,
// and only for this show's items. The token stays in the fragment and in
// this tab's sessionStorage, never in a URL the server sees.

import { Ban, Check, ChevronLeft, ChevronRight, Square } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { parseRemoteLocation, type RemoteCommand } from '../../present'
import '../../present.css'

interface PhoneState {
  index: number
  count: number
  item_id: string | null
  next_item_id: string | null
  title: string
  credit: string
  notes: string
  flag: string
  compare: boolean
  blank: boolean
}

const KEY = 'kist.present.remote'

function readPairing(): { id: string; token: string } | null {
  const fromUrl = parseRemoteLocation(window.location.pathname, window.location.hash)
  if (fromUrl) {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(fromUrl))
    } catch {
      /* private mode: keep it in the page */
    }
    return fromUrl
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) ?? 'null') as { id: string; token: string } | null
    if (saved && window.location.pathname.includes(saved.id)) return saved
  } catch {
    /* nothing kept */
  }
  return null
}

function useRelayThumb(id: string, token: string, itemId: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!itemId) {
      setUrl(null)
      return
    }
    let gone = false
    let made: string | null = null
    void fetch(`/present-relay/${id}/thumb/${itemId}`, { headers: { 'X-Kist-Remote': token } })
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => {
        if (gone || !b) return
        made = URL.createObjectURL(b)
        setUrl(made)
      })
      .catch(() => undefined)
    return () => {
      gone = true
      if (made) URL.revokeObjectURL(made)
    }
  }, [id, token, itemId])
  return url
}

export default function RemotePage() {
  const pairing = useMemo(readPairing, [])
  const [state, setState] = useState<PhoneState | null>(null)
  const [name, setName] = useState('')
  const [status, setStatus] = useState<'connecting' | 'live' | 'ended' | 'offline'>('connecting')
  const [sent, setSent] = useState('')
  const seq = useRef(0)

  // the token out of the address bar (and so out of screenshots and history)
  useEffect(() => {
    if (pairing && window.location.hash) window.history.replaceState(null, '', window.location.pathname)
  }, [pairing])

  useEffect(() => {
    if (!pairing) return
    let alive = true
    const nav = navigator as Navigator & { wakeLock?: { request: (t: 'screen') => Promise<{ release: () => Promise<void> }> } }
    let lock: { release: () => Promise<void> } | null = null
    void nav.wakeLock?.request('screen').then((l) => (lock = l), () => undefined)
    const loop = async () => {
      while (alive) {
        try {
          const r = await fetch(`/present-relay/${pairing.id}/state?after=${seq.current}&wait=20`, {
            headers: { 'X-Kist-Remote': pairing.token },
            cache: 'no-store',
            signal: AbortSignal.timeout(30_000),
          })
          if (r.status === 404) {
            setStatus('ended')
            return
          }
          if (!r.ok) throw new Error(String(r.status))
          const data = (await r.json()) as { seq: number; state: PhoneState | null; name: string }
          seq.current = data.seq
          setName(data.name)
          if (data.state) setState(data.state)
          setStatus('live')
        } catch {
          if (!alive) return
          setStatus('offline')
          await new Promise((res) => setTimeout(res, 2000))
        }
      }
    }
    void loop()
    return () => {
      alive = false
      void lock?.release().catch(() => undefined)
    }
  }, [pairing])

  const send = useCallback(
    async (cmd: RemoteCommand, index?: number) => {
      if (!pairing) return
      navigator.vibrate?.(10)
      try {
        const r = await fetch(`/present-relay/${pairing.id}/cmd`, {
          method: 'POST',
          headers: { 'X-Kist-Remote': pairing.token, 'Content-Type': 'application/json' },
          body: JSON.stringify(index === undefined ? { cmd } : { cmd, index }),
        })
        if (r.status === 404) setStatus('ended')
        else if (r.status === 429) setSent('Slow down a little')
        else setSent('')
      } catch {
        setStatus('offline')
      }
    },
    [pairing],
  )

  const now = useRelayThumb(pairing?.id ?? '', pairing?.token ?? '', state?.item_id ?? null)
  const next = useRelayThumb(pairing?.id ?? '', pairing?.token ?? '', state?.next_item_id ?? null)

  if (!pairing)
    return (
      <main className="present-remote present-remote-msg">
        <h1 className="page-title">Kist remote</h1>
        <p>This link is missing its pairing code. Scan the QR code on the show again.</p>
      </main>
    )
  if (status === 'ended')
    return (
      <main className="present-remote present-remote-msg">
        <h1 className="page-title">The show ended</h1>
        <p>This remote was unpaired or ran out. To carry on, scan a new code from the show (R).</p>
      </main>
    )
  const flags = (state?.flag ?? '').split(',')
  return (
    <main className="present-remote">
      <header className="present-remote-bar">
        <span className="present-name">{name || 'Kist remote'}</span>
        <span className="present-count" role="status">
          {state ? `${state.index + 1} / ${state.count}` : status === 'offline' ? 'Reconnecting…' : 'Connecting…'}
        </span>
      </header>
      <section className="present-remote-now">
        {now ? <img src={now} alt="" /> : <div className="present-empty">{state ? 'No picture' : ''}</div>}
        {next && (
          <div className="present-remote-next">
            <span className="section-label">Next</span>
            <img src={next} alt="" />
          </div>
        )}
      </section>
      {state && (
        <section className="present-remote-text">
          {state.title && <p className="present-cap-title">{state.title}</p>}
          {state.credit && <p className="present-cap-credit">{state.credit}</p>}
          {state.notes && <p className="present-remote-notes">{state.notes}</p>}
          {state.blank && <p className="present-flag">The screen is blank</p>}
        </section>
      )}
      <div className="present-remote-marks">
        {(state?.compare ? [0, 1] : [0]).map((side) => (
          <div key={side} className="present-remote-pair">
            <button type="button" className={`btn-icon${flags[side] === 'pick' ? ' on' : ''}`} aria-pressed={flags[side] === 'pick'} onClick={() => void send('pick', side)}>
              <Check size={20} aria-hidden="true" /> {state?.compare ? (side ? 'Pick right' : 'Pick left') : 'Pick'}
            </button>
            <button type="button" className={`btn-icon${flags[side] === 'reject' ? ' on' : ''}`} aria-pressed={flags[side] === 'reject'} onClick={() => void send('reject', side)}>
              <Ban size={20} aria-hidden="true" /> {state?.compare ? (side ? 'Reject right' : 'Reject left') : 'Reject'}
            </button>
          </div>
        ))}
        <button type="button" className={`btn-icon${state?.blank ? ' on' : ''}`} aria-pressed={!!state?.blank} onClick={() => void send('blank')}>
          <Square size={20} aria-hidden="true" /> Blank
        </button>
      </div>
      <p className="present-remote-sent" role="status">
        {sent}
      </p>
      <nav className="present-remote-nav" aria-label="Slides">
        <button type="button" className="present-remote-big" aria-label="Previous slide" onClick={() => void send('prev')} disabled={!state || state.index === 0}>
          <ChevronLeft size={20} aria-hidden="true" />
        </button>
        <button type="button" className="present-remote-big btn-primary" aria-label="Next slide" onClick={() => void send('next')} disabled={!state || state.index >= state.count - 1}>
          <ChevronRight size={20} aria-hidden="true" />
        </button>
      </nav>
    </main>
  )
}
