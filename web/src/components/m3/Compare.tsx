// Compare (2-4 items: side by side with zoom and pan in step, a wipe for
// two, an overlay with difference) and Survey (fit them all, pick or
// reject each, auto-advance to the next undecided). A full-screen modal
// layer like the preview.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Check, Columns2, Grid2x2, Layers, Link2, Link2Off, SplitSquareHorizontal, X, ZoomIn, ZoomOut } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { thumbUrl, type Item } from '../../api'
import { clampZoom, type Viewport } from '../../board'
import { api3 } from '../../creativeApi'
import { nextUndecided, triageKey } from '../../cull'
import { useAppState } from '../../state'
import { useFocusInOut, useFocusTrap, useInertBackground } from '../../useInert'
import { useLayer, useShortcut } from '../../useShortcuts'
import type { CompareMode } from './Creative'
import { cullText, FlagButtons } from './CullControls'
import { displaySrc, originalSrc } from '../../look'
import { usePatchItem } from './writes3'

type View = 'side' | 'wipe' | 'overlay'

/** An image that shows the display copy, then the original once zoomed in. */
function Sharp({ item, zoomed, className, style }: { item: Item; zoomed: boolean; className?: string; style?: React.CSSProperties }) {
  const [src, setSrc] = useState(thumbUrl(item, 1024) ?? originalSrc(item))
  useEffect(() => {
    const want = zoomed ? originalSrc(item) : displaySrc(item)
    if (!want || want === src) return
    const img = new Image()
    img.onload = () => setSrc(want)
    img.src = want
    return () => {
      img.onload = null
    }
  }, [item, zoomed]) // eslint-disable-line react-hooks/exhaustive-deps
  return <img src={src} alt={item.filename} className={className} style={style} draggable={false} />
}

interface PanZoom {
  onWheel: (e: React.WheelEvent<HTMLElement>) => void
  onPointerDown: (e: React.PointerEvent<HTMLElement>) => void
  onPointerMove: (e: React.PointerEvent<HTMLElement>) => void
  onPointerUp: (e: React.PointerEvent<HTMLElement>) => void
}
interface PanState {
  drag: { x: number; y: number; vp: Viewport } | null
  pointers: Map<number, { x: number; y: number }>
}

const ONE: Viewport = { x: 0, y: 0, zoom: 1 }

export default function Compare({
  ids,
  mode,
  onMode,
  onClose,
}: {
  ids: string[]
  mode: CompareMode
  onMode: (m: CompareMode) => void
  onClose: () => void
}) {
  const { mobile, selectAll } = useAppState()
  const qc = useQueryClient()
  const fetched = useQuery({ queryKey: ['compare', ids], queryFn: () => api3.itemsByIds(ids) })
  // the latest copy of each (flags change while surveying)
  // read every render: a flag set here is patched into ['item', id]
  const [, force] = useState(0)
  const items = (fetched.data?.items ?? []).map((i) => qc.getQueryData<Item>(['item', i.id]) ?? i)
  const { patch } = usePatchItem()
  const [view, setView] = useState<View>('side')
  const [sync, setSync] = useState(true)
  const [vps, setVps] = useState<Record<string, Viewport>>({})
  const [shared, setShared] = useState<Viewport>(ONE)
  const [wipe, setWipe] = useState(50)
  const [diff, setDiff] = useState(false)
  const [opacity, setOpacity] = useState(50)
  const [current, setCurrent] = useState<string | null>(ids[0] ?? null)
  const [hideRejects, setHideRejects] = useState(false)
  const [auto, setAuto] = useState(true)

  const box = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useLayer('preview', true)
  useInertBackground(box)
  useFocusTrap(box, true)
  useFocusInOut(box, true, { initial: () => closeRef.current })

  const vpOf = (id: string) => (sync ? shared : (vps[id] ?? ONE))
  const setVpOf = (id: string) => (v: Viewport) => (sync ? setShared(v) : setVps((m) => ({ ...m, [id]: v })))
  const zoomBy = (f: number) => {
    const z = (v: Viewport): Viewport => {
      const zoom = clampZoom(Math.max(1, v.zoom * f))
      return zoom === 1 ? ONE : { zoom, x: (v.x * zoom) / v.zoom, y: (v.y * zoom) / v.zoom }
    }
    if (sync) setShared(z(shared))
    else setVps((m) => Object.fromEntries(ids.map((id) => [id, z(m[id] ?? ONE)])))
  }

  const flags = Object.fromEntries(items.map((i) => [i.id, i.flag ?? null]))
  const decide = useCallback(
    async (item: Item, change: Parameters<typeof patch>[1]) => {
      const b = await patch(item, change)
      force((n) => n + 1)
      if (!b || mode !== 'survey' || !auto || !('flag' in change) || !change.flag) return
      const after = { ...flags, [item.id]: change.flag }
      const next = nextUndecided(ids, after, item.id)
      if (next) setCurrent(next)
    },
    [patch, mode, auto, flags, ids],
  )

  useShortcut('preview', (e) => {
    if (e.key === 'Escape') {
      onClose()
      return true
    }
    if (e.key === '+' || e.key === '=') zoomBy(1.5)
    else if (e.key === '-') zoomBy(1 / 1.5)
    else if (e.key === '0') {
      setShared(ONE)
      setVps({})
    } else {
      const cur = items.find((i) => i.id === current) ?? items[0]
      const a = triageKey(e)
      if (!a || !cur) return false
      if (a.kind === 'flag') void decide(cur, { flag: a.flag })
      else if (a.kind === 'rate' && mode === 'survey') void decide(cur, { rating: a.rating })
      else if (a.kind === 'label') void decide(cur, { label: cur.label === a.label ? null : a.label })
      else if (a.kind === 'next' || a.kind === 'prev') {
        const list = shown.map((i) => i.id)
        const at = list.indexOf(cur.id)
        setCurrent(list[(at + (a.kind === 'next' ? 1 : -1) + list.length) % list.length] ?? null)
      } else return false
    }
    e.preventDefault()
    return true
  })

  const shown = mode === 'survey' && hideRejects ? items.filter((i) => i.flag !== 'reject') : items
  const two = items.length === 2
  const picks = items.filter((i) => i.flag === 'pick')

  const pane = (item: Item, i: number) => {
    const vp = vpOf(item.id)
    const pz = panZoomFor(item.id, vp, setVpOf(item.id))
    return (
      <figure
        key={item.id}
        className={`cmp-pane${current === item.id ? ' current' : ''}${item.flag === 'reject' ? ' rejected' : ''}`}
        aria-label={`${item.filename}${cullText(item) ? `, ${cullText(item)}` : ''}`}
      >
        <div
          className="cmp-stage"
          onWheel={pz.onWheel}
          onPointerDown={(e) => {
            setCurrent(item.id)
            pz.onPointerDown(e)
          }}
          onPointerMove={pz.onPointerMove}
          onPointerUp={pz.onPointerUp}
          onPointerCancel={pz.onPointerUp}
          onDoubleClick={() => (vp.zoom > 1 ? setVpOf(item.id)(ONE) : setVpOf(item.id)({ zoom: 2.5, x: 0, y: 0 }))}
        >
          <div className="cmp-img" style={{ transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})` }}>
            <Sharp item={item} zoomed={vp.zoom > 1.5} />
          </div>
        </div>
        <figcaption>
          <span className="cmp-n" aria-hidden="true">
            {i + 1}
          </span>
          <span className="fname" title={item.filename}>
            {item.filename}
          </span>
          <span className="meta">{item.width && item.height ? `${item.width}×${item.height}` : ''}</span>
          <FlagButtons flag={item.flag} onFlag={(flag) => void decide(item, { flag })} />
        </figcaption>
      </figure>
    )
  }

  // pointer state per pane survives re-renders
  const panStates = useRef(new Map<string, PanState>())
  const panZoomFor = (id: string, vp: Viewport, set: (v: Viewport) => void) => {
    if (!panStates.current.has(id)) panStates.current.set(id, { drag: null, pointers: new Map() })
    return makePanZoom(vp, set, panStates.current.get(id) as PanState)
  }

  const wipePair = two ? items : []
  const wipeVp = shared

  return (
    <div
      ref={box}
      className={`compare-overlay${mobile ? ' mobile' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={mode === 'survey' ? `Survey ${items.length} items` : `Compare ${items.length} items`}
    >
      <header className="cmp-bar">
        <div className="seg" role="group" aria-label="Mode">
          <button className={mode === 'compare' ? 'on' : ''} aria-pressed={mode === 'compare'} onClick={() => onMode('compare')}>
            <Columns2 size={14} aria-hidden="true" /> Compare
          </button>
          <button className={mode === 'survey' ? 'on' : ''} aria-pressed={mode === 'survey'} onClick={() => onMode('survey')}>
            <Grid2x2 size={14} aria-hidden="true" /> Survey
          </button>
        </div>
        {mode === 'compare' && two && (
          <div className="seg" role="group" aria-label="Layout">
            <button className={view === 'side' ? 'on' : ''} aria-pressed={view === 'side'} onClick={() => setView('side')}>
              Side by side
            </button>
            <button className={view === 'wipe' ? 'on' : ''} aria-pressed={view === 'wipe'} onClick={() => setView('wipe')}>
              <SplitSquareHorizontal size={14} aria-hidden="true" /> Wipe
            </button>
            <button className={view === 'overlay' ? 'on' : ''} aria-pressed={view === 'overlay'} onClick={() => setView('overlay')}>
              <Layers size={14} aria-hidden="true" /> Overlay
            </button>
          </div>
        )}
        {mode === 'compare' && view === 'side' && items.length > 1 && (
          <button className="btn-icon" aria-pressed={sync} onClick={() => setSync(!sync)} title="Zoom and pan all of them together">
            {sync ? <Link2 size={14} aria-hidden="true" /> : <Link2Off size={14} aria-hidden="true" />}
            {sync ? 'Synced' : 'Not synced'}
          </button>
        )}
        {mode === 'compare' && (
          <>
            <button className="icon-btn" aria-label="Zoom out" title="Zoom out (−)" onClick={() => zoomBy(1 / 1.5)}>
              <ZoomOut size={16} aria-hidden="true" />
            </button>
            <button className="icon-btn" aria-label="Zoom in" title="Zoom in (+)" onClick={() => zoomBy(1.5)}>
              <ZoomIn size={16} aria-hidden="true" />
            </button>
            <span className="cmp-zoom" aria-live="polite">
              {Math.round((sync ? shared.zoom : 1) * 100)}%
            </span>
          </>
        )}
        {mode === 'survey' && (
          <>
            <label className="check-inline">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto-advance
            </label>
            <label className="check-inline">
              <input type="checkbox" checked={hideRejects} onChange={(e) => setHideRejects(e.target.checked)} /> Hide rejects
            </label>
            <span className="cmp-status" role="status">
              {picks.length} picked · {items.filter((i) => i.flag === 'reject').length} rejected · {items.filter((i) => !i.flag).length} to go
            </span>
            {picks.length > 0 && (
              <button onClick={() => { selectAll(picks.map((p) => p.id)); onClose() }}>Select the picks</button>
            )}
          </>
        )}
        <span className="grow" />
        <span className="cmp-keys" aria-hidden="true">
          {mode === 'survey' ? 'P pick · X reject · U clear · ←/→ move' : 'wheel/pinch zoom · drag pan · double-click 100%'}
        </span>
        <button ref={closeRef} className="icon-btn cmp-close" onClick={onClose} aria-label="Close compare">
          <X size={18} aria-hidden="true" />
        </button>
      </header>

      {fetched.isLoading && <p className="center">Loading…</p>}

      {mode === 'compare' && view === 'side' && (
        <div className={`cmp-grid n${items.length}`}>{items.map((it, i) => pane(it, i))}</div>
      )}

      {mode === 'compare' && view !== 'side' && wipePair.length === 2 && (
        <WipeView
          a={wipePair[0]}
          b={wipePair[1]}
          vp={wipeVp}
          setVp={setShared}
          wipe={wipe}
          setWipe={setWipe}
          overlay={view === 'overlay'}
          diff={diff}
          setDiff={setDiff}
          opacity={opacity}
          setOpacity={setOpacity}
          onFlag={(it, flag) => void decide(it, { flag })}
        />
      )}

      {mode === 'survey' && (
        <ul className={`survey-grid n${Math.min(shown.length, 9)}`} aria-label="Items in the survey">
          {shown.map((it, i) => (
            <li key={it.id} className={`survey-cell${current === it.id ? ' current' : ''} flag-${it.flag ?? 'none'}`}>
              <button
                className="survey-pick-area"
                aria-label={`${i + 1}. ${it.filename}${cullText(it) ? `, ${cullText(it)}` : ''}${current === it.id ? ', current' : ''}`}
                aria-current={current === it.id ? 'true' : undefined}
                onClick={() => setCurrent(it.id)}
                onDoubleClick={() => void decide(it, { flag: it.flag === 'pick' ? null : 'pick' })}
              >
                <Sharp item={it} zoomed={false} />
                {it.flag && (
                  <span className={`survey-flag ${it.flag}`} aria-hidden="true">
                    {it.flag === 'pick' ? <Check size={16} strokeWidth={3} /> : <Ban size={16} strokeWidth={2.5} />}
                  </span>
                )}
              </button>
              <div className="survey-actions">
                <span className="fname" title={it.filename}>
                  {it.filename}
                </span>
                <FlagButtons flag={it.flag} onFlag={(flag) => { setCurrent(it.id); void decide(it, { flag }) }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Wheel and pointer handlers for one zoomable stage (pinch with two
 * pointers); `state` keeps the pointers between renders. */
function makePanZoom(vp: Viewport, setVp: (v: Viewport) => void, state: PanState): PanZoom {
  return {
    onWheel: (e) => {
      const r = e.currentTarget.getBoundingClientRect()
      const cx = e.clientX - r.left - r.width / 2
      const cy = e.clientY - r.top - r.height / 2
      const zoom = clampZoom(Math.max(1, vp.zoom * Math.exp(-e.deltaY / 300)))
      const k = zoom / vp.zoom
      setVp(zoom === 1 ? ONE : { zoom, x: cx - (cx - vp.x) * k, y: cy - (cy - vp.y) * k })
    },
    onPointerDown: (e) => {
      state.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
      e.currentTarget.setPointerCapture?.(e.pointerId)
      state.drag = { x: e.clientX, y: e.clientY, vp }
    },
    onPointerMove: (e) => {
      if (!state.pointers.has(e.pointerId)) return
      if (state.pointers.size === 2) {
        const [a, b] = [...state.pointers.values()]
        const d0 = Math.hypot(a.x - b.x, a.y - b.y) || 1
        state.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
        const [a1, b1] = [...state.pointers.values()]
        const d1 = Math.hypot(a1.x - b1.x, a1.y - b1.y) || 1
        setVp({ ...vp, zoom: clampZoom(Math.max(1, vp.zoom * (d1 / d0))) })
        state.drag = null
        return
      }
      state.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
      const d = state.drag
      if (!d || d.vp.zoom <= 1) return
      setVp({ ...d.vp, x: d.vp.x + e.clientX - d.x, y: d.vp.y + e.clientY - d.y })
    },
    onPointerUp: (e) => {
      state.pointers.delete(e.pointerId)
      state.drag = null
    },
  }
}

function WipeView({
  a,
  b,
  vp,
  setVp,
  wipe,
  setWipe,
  overlay,
  diff,
  setDiff,
  opacity,
  setOpacity,
  onFlag,
}: {
  a: Item
  b: Item
  vp: Viewport
  setVp: (v: Viewport) => void
  wipe: number
  setWipe: (n: number) => void
  overlay: boolean
  diff: boolean
  setDiff: (v: boolean) => void
  opacity: number
  setOpacity: (n: number) => void
  onFlag: (it: Item, f: 'pick' | 'reject' | null) => void
}) {
  const panState = useRef<PanState>({ drag: null, pointers: new Map() })
  const pz = makePanZoom(vp, setVp, panState.current)
  const stage = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const setFromX = (clientX: number) => {
    const r = stage.current?.getBoundingClientRect()
    if (!r) return
    setWipe(Math.round(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100))))
  }
  const t = `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})`
  return (
    <div className="cmp-wipe">
      <div
        ref={stage}
        className="cmp-stage wipe-stage"
        onWheel={pz.onWheel}
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest('.wipe-handle')) {
            dragging.current = true
            e.currentTarget.setPointerCapture(e.pointerId)
            return
          }
          pz.onPointerDown(e)
        }}
        onPointerMove={(e) => (dragging.current ? setFromX(e.clientX) : pz.onPointerMove(e))}
        onPointerUp={(e) => {
          dragging.current = false
          pz.onPointerUp(e)
        }}
      >
        <div className="cmp-layer">
          <div className="cmp-img" style={{ transform: t }}>
            <Sharp item={a} zoomed={vp.zoom > 1.5} />
          </div>
        </div>
        {/* the clip is on an untransformed layer, so the wipe line stays put while zoomed */}
        <div
          className="cmp-layer"
          style={{
            clipPath: overlay ? undefined : `inset(0 0 0 ${wipe}%)`,
            opacity: overlay && !diff ? opacity / 100 : 1,
            mixBlendMode: overlay && diff ? 'difference' : undefined,
          }}
        >
          <div className="cmp-img" style={{ transform: t }}>
            <Sharp item={b} zoomed={vp.zoom > 1.5} />
          </div>
        </div>
        {!overlay && <div className="wipe-handle" style={{ left: `${wipe}%` }} aria-hidden="true" />}
      </div>
      <div className="cmp-controls">
        <span className="fname">A: {a.filename}</span>
        <FlagButtons flag={a.flag} onFlag={(f) => onFlag(a, f)} />
        {overlay ? (
          <>
            <label className="check-inline">
              <input type="checkbox" checked={diff} onChange={(e) => setDiff(e.target.checked)} /> Difference
            </label>
            {!diff && (
              <label className="range-inline">
                Opacity of B
                <input type="range" min={0} max={100} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} aria-valuetext={`${opacity}%`} />
              </label>
            )}
          </>
        ) : (
          <label className="range-inline">
            Wipe
            <input type="range" min={0} max={100} value={wipe} onChange={(e) => setWipe(Number(e.target.value))} aria-valuetext={`${wipe}% of A shown`} />
          </label>
        )}
        <span className="fname">B: {b.filename}</span>
        <FlagButtons flag={b.flag} onFlag={(f) => onFlag(b, f)} />
      </div>
    </div>
  )
}
