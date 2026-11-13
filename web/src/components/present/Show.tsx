// Present (Kist 2.0 F2): the show. Full screen, never cropped (fit), a
// grey or black surround, the original image in its own colour profile,
// captions with title and credit (file names only when asked), compare
// slides side by side, the next two slides and the one before preloaded.
//
// Keys: → ↓ Space PageDown next · ← ↑ PageUp Backspace previous · Home/End ·
// a number then Enter jumps · P pick · X reject · N note · J join with the
// next slide / split · C captions · F file names · G grey/black · B blank ·
// T pause the timer · V presenter view · R phone remote · ? keys · Esc out.
// The cursor and every control hide after a moment; they come back when the
// mouse moves. On a phone: tap the left or right third to go back or on, the
// middle for the controls; swipe works too.

import { useQueryClient } from '@tanstack/react-query'
import {
  Ban,
  Captions,
  Check,
  ChevronLeft,
  ChevronRight,
  Columns2,
  MonitorPlay,
  Pause,
  Play,
  Smartphone,
  StickyNote,
  Square,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { newBatchId } from '../../api'
import {
  captionFor,
  channelName,
  clampIndex,
  formatBytes,
  formatClock,
  joinWithNext,
  kenBurnsAllowed,
  nextFlag,
  notesFor,
  offlinePlan,
  phoneState,
  preloadUrls,
  presenterUrl,
  showItemIds,
  slideSeconds,
  splitCompare,
  typedJump,
  updateItem,
  type PresentState,
  type PresenterMessage,
  type RemoteCommand,
  type Slide,
  type SlideItem,
  type ShowOptions,
} from '../../present'
import { presentApi } from '../../presentApi'
import { keepOffline, type OfflineProgress } from '../../presentOffline'
import { useCaps } from '../../data'
import { useLayer, useShortcut } from '../../useShortcuts'
import { describeError } from '../../writes'
import { presentAids } from './aids'
import { PairPanel, type PairState } from './PairPanel'
import type { Running } from './PresentHost'

const HUD_MS = 2500
const REST_MS = 2000 // the grey pause between gesture poses
const TOAST_MS = 1600

function SlideImage({ it, index, kb, seconds }: { it: SlideItem; index: number; kb: boolean; seconds: number }) {
  // the 1024 thumbnail shows at once; the full image takes its place, in
  // the same box, as soon as it has loaded (preloading makes that instant)
  const [loaded, setLoaded] = useState(false)
  let media: ReactNode
  if (it.kind === 'video' && it.src) {
    media = <video className="present-media" src={it.src} controls playsInline preload="auto" />
  } else if (it.src) {
    media = (
      <div className="present-layers">
        {it.thumb && !loaded && <img className="present-media present-under" src={it.thumb} alt="" aria-hidden="true" draggable={false} />}
        <img
          className="present-media"
          src={it.src}
          alt={it.title || `Slide ${index + 1}`}
          decoding="async"
          draggable={false}
          onLoad={() => setLoaded(true)}
        />
      </div>
    )
  } else if (it.thumb) {
    media = <img className="present-media" src={it.thumb} alt={it.title || `Slide ${index + 1}`} draggable={false} />
  } else {
    media = <div className="present-empty">No picture for this item</div>
  }
  if (kb) {
    media = (
      <div
        className={`present-kb present-kb-${index % 2 ? 'b' : 'a'}`}
        style={{ animationDuration: `${Math.max(6, seconds || 12)}s` }}
      >
        {media}
      </div>
    )
  }
  return <>{presentAids.wrap ? presentAids.wrap(media, it) : media}</>
}

function SlideView({
  slide,
  index,
  opts,
  onSide,
  sideLabel,
}: {
  slide: Slide
  index: number
  opts: ShowOptions
  onSide?: (side: number) => void
  sideLabel?: boolean
}) {
  const kb = kenBurnsAllowed(slide, opts)
  return (
    <div className={`present-slide${slide.kind === 'compare' ? ' present-slide-compare' : ''}`}>
      {slide.items.map((it, side) => {
        const cap = captionFor(it, opts)
        return (
          <figure key={it.id} className="present-figure" onClick={onSide ? (e) => (e.stopPropagation(), onSide(side)) : undefined}>
            <div className="present-frame">
              <SlideImage it={it} index={index} kb={kb} seconds={slideSeconds(opts)} />
              {sideLabel && <span className="present-side-label">{side + 1}</span>}
            </div>
            {cap && (
              <figcaption className="present-caption">
                {cap.title && <span className="present-cap-title">{cap.title}</span>}
                {cap.credit && <span className="present-cap-credit">{cap.credit}</span>}
                {cap.filename && <span className="present-cap-file">{cap.filename}</span>}
              </figcaption>
            )}
          </figure>
        )
      })}
    </div>
  )
}

type Mark = 'pick' | 'reject' | 'note'

export default function Show({ resolved, options, offlineCap, onClose }: Running & { onClose: () => void }) {
  const [slides, setSlides] = useState<Slide[]>(resolved.slides)
  const [index, setIndex] = useState(0)
  const [opts, setOpts] = useState<ShowOptions>(options)
  const [hud, setHud] = useState(true)
  const [blank, setBlank] = useState(false)
  const [resting, setResting] = useState(false)
  const [paused, setPaused] = useState(false)
  const [choose, setChoose] = useState<Mark | null>(null)
  const [note, setNote] = useState<{ id: string; text: string } | null>(null)
  const [toast, setToast] = useState('')
  const [help, setHelp] = useState(false)
  const [pairOpen, setPairOpen] = useState(false)
  const [pair, setPair] = useState<PairState>({ path: null, connected: false, expiresAt: null, error: '', busy: false })
  const [remoteId, setRemoteId] = useState<string | null>(null)
  const [offline, setOffline] = useState<OfflineProgress | null>(null)
  const [slideStart, setSlideStart] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())
  const startedAt = useRef(Date.now())
  const jump = useRef('')
  const rootRef = useRef<HTMLDivElement>(null)
  const hudTimer = useRef<number | undefined>(undefined)
  const toastTimer = useRef<number | undefined>(undefined)
  const preloaded = useRef(new Map<string, HTMLImageElement>())
  const showKey = useMemo(() => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`, [])
  const caps = useCaps()
  const qc = useQueryClient()
  const batch = useMemo(() => newBatchId(), [])

  const count = slides.length
  const slide = slides[clampIndex(index, count)]
  const seconds = slideSeconds(opts)

  useLayer('modal', true)

  const say = useCallback((text: string) => {
    setToast(text)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(''), TOAST_MS)
  }, [])

  const poke = useCallback(() => {
    setHud(true)
    window.clearTimeout(hudTimer.current)
    hudTimer.current = window.setTimeout(() => setHud(false), HUD_MS)
  }, [])

  // ---- full screen, screen awake, landscape on phones ----
  useEffect(() => {
    const el = rootRef.current
    el?.focus()
    let lock: { release: () => Promise<void> } | null = null
    void el?.requestFullscreen?.().then(
      () => {
        const o = screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> }
        void o?.lock?.('landscape').catch(() => undefined)
      },
      () => undefined,
    )
    const nav = navigator as Navigator & { wakeLock?: { request: (t: 'screen') => Promise<{ release: () => Promise<void> }> } }
    void nav.wakeLock?.request('screen').then((l) => (lock = l), () => undefined)
    poke()
    return () => {
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined)
      void lock?.release().catch(() => undefined)
      window.clearTimeout(hudTimer.current)
      window.clearTimeout(toastTimer.current)
    }
  }, [poke])

  // the picks and notes made in the show show up in the grid afterwards
  useEffect(() => () => void qc.invalidateQueries({ queryKey: ['items'] }), [qc])

  // ---- offline copy ----
  useEffect(() => {
    if (!offlineCap) return
    const plan = offlinePlan(resolved.slides, offlineCap)
    keepOffline(plan.urls, offlineCap, setOffline)
  }, [offlineCap, resolved.slides])

  // ---- fast advance: warm the next two slides and the one before ----
  useEffect(() => {
    const keep = preloadUrls(slides, index)
    const cache = preloaded.current
    for (const url of keep) {
      if (cache.has(url)) continue
      const img = new Image()
      img.decoding = 'async'
      img.src = url
      void img.decode?.().catch(() => undefined)
      cache.set(url, img)
    }
    for (const url of [...cache.keys()]) if (!keep.includes(url) && cache.size > 6) cache.delete(url)
  }, [slides, index])

  const go = useCallback(
    (i: number) => {
      const next = clampIndex(i, count)
      setIndex(next)
      setSlideStart(Date.now())
      setResting(false)
      setChoose(null)
    },
    [count],
  )
  const step = useCallback(
    (d: number) => {
      if (d > 0 && index >= count - 1) return say('That’s the last slide')
      if (d < 0 && index <= 0) return
      go(index + d)
    },
    [index, count, go, say],
  )

  // ---- timed advance and the gesture timer ----
  useEffect(() => {
    if (!seconds || paused || blank || index >= count - 1) return
    const left = slideStart + seconds * 1000 - Date.now()
    const t = window.setTimeout(
      () => {
        if (opts.gesture_seconds) {
          setResting(true)
          window.setTimeout(() => go(index + 1), REST_MS)
        } else go(index + 1)
      },
      Math.max(0, left),
    )
    return () => window.clearTimeout(t)
  }, [seconds, paused, blank, index, count, slideStart, opts.gesture_seconds, go])
  // pausing keeps the time left
  const pausedAt = useRef<number | null>(null)
  useEffect(() => {
    if (paused) pausedAt.current = Date.now()
    else if (pausedAt.current) {
      const gap = Date.now() - pausedAt.current
      pausedAt.current = null
      setSlideStart((s) => s + gap)
    }
  }, [paused])
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(t)
  }, [])

  // ---- marks: pick / reject / note ----
  const mark = useCallback(
    async (what: Mark, side?: number) => {
      if (!slide) return
      if (!caps.canEdit) return say('This token can’t mark items')
      if (slide.kind === 'compare' && side === undefined) {
        setChoose(what)
        poke()
        return
      }
      setChoose(null)
      const it = slide.items[side ?? 0]
      if (!it) return
      if (what === 'note') {
        setNote({ id: it.id, text: '' })
        return
      }
      const flag = nextFlag(it.flag, what)
      setSlides((s) => updateItem(s, it.id, { flag }))
      say(flag === 'pick' ? 'Picked' : flag === 'reject' ? 'Rejected' : what === 'pick' ? 'Pick cleared' : 'Reject cleared')
      try {
        await presentApi.setFlag(it.id, flag, batch)
      } catch (e) {
        setSlides((s) => updateItem(s, it.id, { flag: it.flag }))
        say(`Not saved: ${describeError(e)}`)
      }
    },
    [slide, caps.canEdit, say, poke, batch],
  )

  const saveNote = async () => {
    if (!note) return
    const text = note.text.trim()
    setNote(null)
    if (!text) return
    try {
      await presentApi.addNote(note.id, text)
      say('Note saved on the item')
    } catch (e) {
      say(`Note not saved: ${describeError(e)}`)
    }
  }

  const toggleJoin = () => {
    if (!slide) return
    if (slide.kind === 'compare') setSlides((s) => splitCompare(s, index))
    else if (index < count - 1) setSlides((s) => joinWithNext(s, index))
    else say('No next slide to put beside this one')
  }

  // ---- presenter window (BroadcastChannel) ----
  const channel = useMemo(() => (typeof BroadcastChannel === 'function' ? new BroadcastChannel(channelName(showKey)) : null), [showKey])
  useEffect(() => () => channel?.close(), [channel])
  const state: PresentState = useMemo(
    () => ({
      name: resolved.name,
      index,
      count,
      slide: slide ?? null,
      next: slides[index + 1] ?? null,
      notes: notesFor(slide),
      startedAt: startedAt.current,
      slideEndsAt: seconds && !paused && index < count - 1 ? slideStart + seconds * 1000 : null,
      paused,
      blank,
      options: opts,
    }),
    [resolved.name, index, count, slide, slides, seconds, paused, slideStart, blank, opts],
  )
  const stateRef = useRef(state)
  stateRef.current = state
  const pairRef = useRef(pair)
  pairRef.current = pair
  const post = useCallback((m: PresenterMessage | { type: 'remote'; path: string | null; connected: boolean }) => channel?.postMessage(m), [channel])
  useEffect(() => post({ type: 'state', state }), [post, state])
  useEffect(() => post({ type: 'remote', path: pair.path, connected: pair.connected }), [post, pair.path, pair.connected])

  const openPresenter = () => {
    const w = window.open(presenterUrl(showKey), `kist-presenter-${showKey}`, 'popup=yes,width=1200,height=760')
    if (!w) say('The browser blocked the presenter window: allow pop-ups for Kist')
  }

  // ---- phone remote ----
  const doPair = useCallback(async () => {
    setPair((p) => ({ ...p, busy: true, error: '' }))
    try {
      if (remoteId) await presentApi.unpair(remoteId).catch(() => undefined)
      const r = await presentApi.pair(resolved.name, showItemIds(slides))
      setRemoteId(r.id)
      setPair({ path: r.path, connected: false, expiresAt: r.expires_at, error: '', busy: false })
    } catch (e) {
      setPair((p) => ({ ...p, busy: false, error: describeError(e) }))
    }
  }, [remoteId, resolved.name, slides])
  const doUnpair = useCallback(async () => {
    const id = remoteId
    setRemoteId(null)
    setPair({ path: null, connected: false, expiresAt: null, error: '', busy: false })
    if (id) await presentApi.unpair(id).catch(() => undefined)
  }, [remoteId])
  // unpair when the show ends
  const remoteRef = useRef(remoteId)
  remoteRef.current = remoteId
  useEffect(() => () => void (remoteRef.current && presentApi.unpair(remoteRef.current).catch(() => undefined)), [])

  const command = useCallback(
    (cmd: RemoteCommand | string, i?: number) => {
      if (cmd === 'next') step(1)
      else if (cmd === 'prev') step(-1)
      else if (cmd === 'first') go(0)
      else if (cmd === 'last') go(count - 1)
      else if (cmd === 'goto' && typeof i === 'number') go(i)
      else if (cmd === 'blank') setBlank((b) => !b)
      else if (cmd === 'pick' || cmd === 'reject') void mark(cmd, slide?.kind === 'compare' ? (i ?? 0) : 0)
    },
    [step, go, count, mark, slide],
  )
  const commandRef = useRef(command)
  commandRef.current = command

  useEffect(() => {
    if (!channel) return
    channel.onmessage = (e: MessageEvent) => {
      const m = e.data as PresenterMessage | { type: 'pair' } | { type: 'unpair' }
      if (m.type === 'hello') {
        post({ type: 'state', state: stateRef.current })
        post({ type: 'remote', path: pairRef.current.path, connected: pairRef.current.connected })
      } else if (m.type === 'cmd') commandRef.current(m.cmd, m.index)
      else if (m.type === 'pair') void doPair()
      else if (m.type === 'unpair') void doUnpair()
    }
  }, [channel, post, doPair, doUnpair])

  useEffect(() => {
    if (!remoteId) return
    const ctl = new AbortController()
    let after = 0
    let alive = true
    const loop = async () => {
      while (alive) {
        try {
          const r = await presentApi.poll(remoteId, after, ctl.signal)
          after = r.seq
          setPair((p) => (p.connected === r.connected ? p : { ...p, connected: r.connected }))
          for (const e of r.events) commandRef.current(e.cmd, e.index)
        } catch (e) {
          if (!alive) return
          if ((e as { status?: number }).status === 404) {
            setRemoteId(null)
            setPair({ path: null, connected: false, expiresAt: null, error: 'The pairing ended. Pair again to use the phone.', busy: false })
            return
          }
          await new Promise((r) => setTimeout(r, 3000))
        }
      }
    }
    void loop()
    return () => {
      alive = false
      ctl.abort()
    }
  }, [remoteId])
  useEffect(() => {
    if (!remoteId) return
    const t = window.setTimeout(() => void presentApi.publish(remoteId, phoneState(state)).catch(() => undefined), 80)
    return () => window.clearTimeout(t)
  }, [remoteId, state])

  // ---- keys ----
  useShortcut('modal', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return false
    const k = e.key
    if (note) return false // the note field has the keys
    if (k === 'Escape') {
      e.preventDefault()
      if (choose) setChoose(null)
      else if (help) setHelp(false)
      else if (pairOpen) setPairOpen(false)
      else if (blank) setBlank(false)
      else onClose()
      return true
    }
    if (choose && (k === '1' || k === '2')) {
      // the key mustn't also land in the note field this opens
      e.preventDefault()
      void mark(choose, Number(k) - 1)
      return true
    }
    const j = typedJump(jump.current, k)
    jump.current = j.buffer
    if (/^[0-9]$/.test(k)) {
      e.preventDefault()
      say(`Go to ${j.buffer}…`)
      return true
    }
    if (j.go !== null) {
      e.preventDefault()
      go(j.go)
      return true
    }
    if (presentAids.onKey?.(e as unknown as KeyboardEvent)) return true
    switch (k) {
      case 'ArrowRight':
      case 'ArrowDown':
      case 'PageDown':
      case ' ':
        step(1)
        break
      case 'ArrowLeft':
      case 'ArrowUp':
      case 'PageUp':
      case 'Backspace':
        step(-1)
        break
      case 'Home':
        go(0)
        break
      case 'End':
        go(count - 1)
        break
      default:
        switch (k.toLowerCase()) {
          case 'p':
            void mark('pick')
            break
          case 'x':
            void mark('reject')
            break
          case 'n':
            void mark('note')
            break
          case 'j':
            toggleJoin()
            break
          case 'c':
            setOpts((o) => ({ ...o, captions: !o.captions }))
            break
          case 'f':
            setOpts((o) => ({ ...o, filenames: !o.filenames }))
            break
          case 'g':
            setOpts((o) => ({ ...o, surround: o.surround === 'grey' ? 'black' : 'grey' }))
            break
          case 'b':
          case '.':
            setBlank((b) => !b)
            break
          case 't':
            if (seconds) setPaused((p) => !p)
            break
          case 'v':
            openPresenter()
            break
          case 'r':
            setPairOpen((o) => !o)
            break
          case '?':
          case 'h':
            setHelp((h) => !h)
            break
          default:
            return false
        }
    }
    e.preventDefault()
    return true
  })

  // ---- touch: thirds and swipes ----
  const touch = useRef<{ x: number; y: number; t: number } | null>(null)
  const onStageClick = (e: React.MouseEvent) => {
    if (choose) return
    const w = window.innerWidth
    if (e.clientX < w / 3) step(-1)
    else if (e.clientX > (2 * w) / 3) step(1)
    else if (hud) setHud(false)
    else poke()
  }

  const timeLeft = state.slideEndsAt ? state.slideEndsAt - now : null
  const it0 = slide?.items[0]
  const flagOf = (it: SlideItem) => (it.flag === 'pick' ? 'Picked' : it.flag === 'reject' ? 'Rejected' : '')

  return (
    <div
      ref={rootRef}
      data-present-key={showKey}
      className={`present-show present-surround-${opts.surround}${hud ? ' present-hud-on' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={`Presenting ${resolved.name}`}
      tabIndex={-1}
      onMouseMove={poke}
      onTouchStart={(e) => (touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() })}
      onTouchEnd={(e) => {
        const s = touch.current
        touch.current = null
        if (!s) return
        const dx = e.changedTouches[0].clientX - s.x
        const dy = e.changedTouches[0].clientY - s.y
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) && Date.now() - s.t < 600) {
          e.preventDefault()
          step(dx < 0 ? 1 : -1)
        }
      }}
    >
      <div className="present-stage" onClick={onStageClick}>
        {slide && !blank && !resting && (
          <SlideView key={`${index}:${slide.items.map((i) => i.id).join()}`} slide={slide} index={index} opts={opts} onSide={choose ? (s) => void mark(choose, s) : undefined} sideLabel={!!choose} />
        )}
      </div>

      {/* the gesture timer: the one thing that stays on screen (opt-in) */}
      {opts.gesture_seconds && timeLeft !== null && !resting && (
        <div className={`present-gesture${paused ? ' paused' : ''}`} aria-hidden="true">
          {formatClock(timeLeft + 999)}
        </div>
      )}

      {choose && (
        <div className="present-choose float-surface" role="status">
          {choose === 'note' ? 'Note on which?' : choose === 'pick' ? 'Pick which?' : 'Reject which?'} Press 1 (left) or 2 (right), or click it.
        </div>
      )}

      {note && (
        <form
          className="present-note float-surface"
          onSubmit={(e) => {
            e.preventDefault()
            void saveNote()
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <label htmlFor="present-note-text" className="section-label">
            Note on this image
          </label>
          <input
            id="present-note-text"
            autoFocus
            value={note.text}
            maxLength={4000}
            placeholder="Saved on the item, in its history"
            onChange={(e) => setNote({ ...note, text: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.stopPropagation()
                setNote(null)
              }
            }}
          />
          <button type="submit" className="btn-primary">
            Save
          </button>
        </form>
      )}

      <div className="present-toast" role="status" aria-live="polite">
        {toast}
      </div>

      {hud && (
        <>
          <div className="present-hud float-surface" role="toolbar" aria-label="Show controls" onClick={(e) => e.stopPropagation()}>
            <span className="present-hud-info">
            <span className="present-name">{resolved.name}</span>
            <span className="present-count">
              {index + 1} / {count}
            </span>
            {slide?.items.map((it) => flagOf(it) && <span key={it.id} className="present-flag">{flagOf(it)}</span>)}
            {offline && (
              <span className="present-offline" title="Kept on this device for use without a connection">
                {offline.finished ? `Offline: ${offline.done} of ${offline.total} images, ${formatBytes(offline.bytes)}` : `Saving offline ${offline.done}/${offline.total}`}
              </span>
            )}
            {pair.connected && <span className="present-offline">Phone connected</span>}
            </span>
            <button type="button" className="icon-btn" aria-label="Previous slide" title="Previous (←)" onClick={() => step(-1)} disabled={index === 0}>
              <ChevronLeft size={20} aria-hidden="true" />
            </button>
            <button type="button" className="icon-btn" aria-label="Next slide" title="Next (→ or Space)" onClick={() => step(1)} disabled={index >= count - 1}>
              <ChevronRight size={20} aria-hidden="true" />
            </button>
            <span className="present-sep" aria-hidden="true" />
            {caps.canEdit && (
              <>
                <button type="button" className={`icon-btn${it0?.flag === 'pick' && slide?.kind === 'single' ? ' on' : ''}`} aria-label="Pick" title="Pick (P)" onClick={() => void mark('pick')}>
                  <Check size={16} aria-hidden="true" />
                </button>
                <button type="button" className={`icon-btn${it0?.flag === 'reject' && slide?.kind === 'single' ? ' on' : ''}`} aria-label="Reject" title="Reject (X)" onClick={() => void mark('reject')}>
                  <Ban size={16} aria-hidden="true" />
                </button>
                <button type="button" className="icon-btn" aria-label="Note" title="A note on the image (N)" onClick={() => void mark('note')}>
                  <StickyNote size={16} aria-hidden="true" />
                </button>
              </>
            )}
            <button type="button" className={`icon-btn${slide?.kind === 'compare' ? ' on' : ''}`} aria-label={slide?.kind === 'compare' ? 'Split this pair' : 'Side by side with the next slide'} title="Side by side with the next slide / split (J)" onClick={toggleJoin}>
              <Columns2 size={16} aria-hidden="true" />
            </button>
            <button type="button" className={`icon-btn${opts.captions ? ' on' : ''}`} aria-label="Captions" aria-pressed={opts.captions} title="Captions: title and credit (C)" onClick={() => setOpts((o) => ({ ...o, captions: !o.captions }))}>
              <Captions size={16} aria-hidden="true" />
            </button>
            <button type="button" className={`icon-btn${blank ? ' on' : ''}`} aria-label="Blank the screen" aria-pressed={blank} title="Blank the screen (B)" onClick={() => setBlank((b) => !b)}>
              <Square size={16} aria-hidden="true" />
            </button>
            {seconds > 0 && (
              <button type="button" className="icon-btn" aria-label={paused ? 'Resume the timer' : 'Pause the timer'} title={`${paused ? 'Resume' : 'Pause'} the timer (T)`} onClick={() => setPaused((p) => !p)}>
                {paused ? <Play size={16} aria-hidden="true" /> : <Pause size={16} aria-hidden="true" />}
              </button>
            )}
            {presentAids.Toolbar && slide && <presentAids.Toolbar items={slide.items} />}
            <span className="present-sep" aria-hidden="true" />
            <button type="button" className="icon-btn" aria-label="Presenter view" title="Presenter view in a second window: notes, next slide, timer (V)" onClick={openPresenter}>
              <MonitorPlay size={16} aria-hidden="true" />
            </button>
            {caps.canEdit && (
              <button type="button" className={`icon-btn${pair.path ? ' on' : ''}`} aria-label="Phone remote" title="Use your phone as the remote (R)" onClick={() => setPairOpen((o) => !o)}>
                <Smartphone size={16} aria-hidden="true" />
              </button>
            )}
            <button type="button" className="present-keys-btn" title="Keys (?)" onClick={() => setHelp((h) => !h)}>
              ?
            </button>
            <button type="button" className="icon-btn" aria-label="End the show" title="End the show (Esc)" onClick={onClose}>
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        </>
      )}

      {pairOpen && <PairPanel pair={pair} onPair={() => void doPair()} onUnpair={() => void doUnpair()} onClose={() => setPairOpen(false)} />}

      {help && (
        <div className="present-help float-surface" role="dialog" aria-label="Keys" onClick={(e) => e.stopPropagation()}>
          <dl>
            <dt>→ Space</dt>
            <dd>Next</dd>
            <dt>←</dt>
            <dd>Previous</dd>
            <dt>14 Enter</dt>
            <dd>Go to slide 14</dd>
            <dt>P / X</dt>
            <dd>Pick / reject (again: clear)</dd>
            <dt>N</dt>
            <dd>Note on the image</dd>
            <dt>J</dt>
            <dd>Side by side with the next / split</dd>
            <dt>C / F</dt>
            <dd>Captions / file names</dd>
            <dt>G</dt>
            <dd>Grey or black surround</dd>
            <dt>B</dt>
            <dd>Blank screen</dd>
            <dt>T</dt>
            <dd>Pause the timer</dd>
            <dt>V / R</dt>
            <dd>Presenter view / phone remote</dd>
            <dt>Esc</dt>
            <dd>End the show</dd>
          </dl>
        </div>
      )}
    </div>
  )
}
