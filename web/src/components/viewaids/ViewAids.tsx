// Viewing aids (F5) for the preview, compare and Present: the frame that
// draws them, the View menu (desktop popover, phone panel) and the keys.
// Logic and state live in ../../viewAids.ts.
//
// For Present (F2), the hook is:
//   const [aids, setAids] = useViewAids()
//   useViewAidKeys('preview', true)            // G V H L T 1, Escape leaves 100 %/tile
//   <ViewAidsFrame item={item} aids={aids}>{<img …/>}</ViewAidsFrame>
//   <ViewAidsMenu aids={aids} setAids={setAids} />   // optional toolbar button
// The frame applies greyscale/posterise/mirror to its <img>, draws the grid
// over the picture, and swaps in the repeat-tile or true-100 % view.

import { Eye, X } from 'lucide-react'
import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react'
import { displaySrc, originalSrc, type LookItem } from '../../look'
import { useShortcut } from '../../useShortcuts'
import type { Layer } from '../../shortcuts'
import {
  actualSize,
  actualSource,
  aidsSummary,
  ALL_FEATURES,
  anyAid,
  clampGridN,
  containedRect,
  DEFAULT_AIDS,
  escapeAids,
  filterKind,
  GRID_N_MAX,
  GRID_N_MIN,
  gridLines,
  LUMA_MATRIX,
  posterizeTable,
  tileCells,
  viewAidKey,
  viewAidsStore,
  type AidFeatures,
  type Rect,
  type ViewAids,
} from '../../viewAids'

export function useViewAids(): [ViewAids, (a: ViewAids | ((v: ViewAids) => ViewAids)) => void] {
  const aids = useSyncExternalStore(viewAidsStore.subscribe, viewAidsStore.get, viewAidsStore.get)
  return [aids, viewAidsStore.set]
}

/** The aids' keys on a shortcut layer. Call it after the screen's own
 * useShortcut so it is offered keys first (newest handler wins). */
export function useViewAidKeys(layer: Layer, enabled: boolean, features: AidFeatures = ALL_FEATURES) {
  useShortcut(
    layer,
    (e) => {
      const cur = viewAidsStore.get()
      const next = e.key === 'Escape' ? escapeAids(cur) : viewAidKey(e, cur, features)
      if (!next) return false
      e.preventDefault()
      viewAidsStore.set(next)
      return true
    },
    enabled,
  )
}

/** The SVG filters, by id prefix: value (luminance in linear light, so
 * the grey has the colour's real lightness) and n-step posterise. */
export function ViewAidsDefs({ id }: { id: string }) {
  return (
    <svg className="va-defs" aria-hidden="true" focusable="false" width="0" height="0">
      <defs>
        <filter id={`${id}-grey`} colorInterpolationFilters="linearRGB">
          <feColorMatrix type="matrix" values={LUMA_MATRIX} />
        </filter>
        {[3, 5].map((n) => (
          <filter key={n} id={`${id}-post${n}`} colorInterpolationFilters="linearRGB">
            <feColorMatrix type="matrix" values={LUMA_MATRIX} />
            {/* bands in sRGB-encoded grey, which is close to even lightness steps */}
            <feComponentTransfer colorInterpolationFilters="sRGB">
              <feFuncR type="discrete" tableValues={posterizeTable(n)} />
              <feFuncG type="discrete" tableValues={posterizeTable(n)} />
              <feFuncB type="discrete" tableValues={posterizeTable(n)} />
            </feComponentTransfer>
          </filter>
        ))}
      </defs>
    </svg>
  )
}

/** CSS custom properties a scope sets for its pictures (va.css reads them). */
export function aidsStyle(aids: ViewAids, defsId: string): CSSProperties {
  const f = filterKind(aids)
  return {
    ['--va-filter' as string]: f ? `url(#${defsId}-${f})` : 'none',
    ['--va-flip' as string]: aids.mirror ? 'scaleX(-1)' : 'none',
  }
}

/** Grid lines over the picture inside `scope` (the first img/video/.va-tiles
 * in it), re-measured when the box changes or `measureKey` does. */
export function ViewAidsGrid({ aids, measureKey = '' }: { aids: ViewAids; measureKey?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [rect, setRect] = useState<Rect | null>(null)
  const lines = gridLines(aids)
  useLayoutEffect(() => {
    const host = ref.current?.parentElement
    if (!host || !lines.length) return
    const measure = () => {
      const pic = host.querySelector<HTMLElement>('.va-tiles, .va-actual-img, img, video')
      if (!pic) return setRect(null)
      const h = host.getBoundingClientRect()
      const r = pic.getBoundingClientRect()
      let nat: [number, number] = [0, 0]
      if (pic instanceof HTMLImageElement) nat = [pic.naturalWidth, pic.naturalHeight]
      else if (pic instanceof HTMLVideoElement) nat = [pic.videoWidth, pic.videoHeight]
      // a transformed picture (compare zoom) has its scale in r already
      const c = nat[0] ? containedRect(r.width, r.height, nat[0], nat[1]) : { x: 0, y: 0, w: r.width, h: r.height }
      setRect({ x: r.left - h.left + c.x, y: r.top - h.top + c.y, w: c.w, h: c.h })
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(host)
    const pic = host.querySelector('img, video')
    if (pic) ro.observe(pic)
    pic?.addEventListener('load', measure)
    window.addEventListener('resize', measure)
    return () => {
      ro.disconnect()
      pic?.removeEventListener('load', measure)
      window.removeEventListener('resize', measure)
    }
  }, [lines.length, measureKey]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!lines.length) return null
  return (
    <div ref={ref} className="va-grid-host" aria-hidden="true">
      {rect && (
        <svg className="va-grid" style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }} viewBox="0 0 1 1" preserveAspectRatio="none">
          {lines.map((f) => (
            <g key={f}>
              <line x1={f} y1={0} x2={f} y2={1} />
              <line x1={0} y1={f} x2={1} y2={f} />
            </g>
          ))}
          <rect x={0} y={0} width={1} height={1} />
        </svg>
      )}
    </div>
  )
}

type FrameItem = LookItem & { filename?: string }

/** The picture with the aids applied; `children` is the normal image. */
export function ViewAidsFrame({
  item,
  aids,
  children,
  features = ALL_FEATURES,
  className = '',
}: {
  item: FrameItem
  aids: ViewAids
  children: ReactNode
  features?: AidFeatures
  className?: string
}) {
  const defsId = `va${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  const tile = features.tile ? aids.tile : 0
  const actual = features.actual && aids.actual
  const moved = aids.mirror || !!tile || actual
  return (
    <div
      className={`va-frame${moved ? ' va-moved' : ''}${anyAid(aids) ? ' va-on' : ''}${className ? ` ${className}` : ''}`}
      style={aidsStyle(aids, defsId)}
    >
      <ViewAidsDefs id={defsId} />
      {actual ? <ActualView item={item} aids={aids} /> : tile ? <TileView item={item} n={tile} aids={aids} /> : children}
      {/* 100 % draws its own grid inside the scrolling picture */}
      {!actual && <ViewAidsGrid aids={aids} measureKey={`${item.id}:${tile}`} />}
    </div>
  )
}

function TileView({ item, n, aids }: { item: FrameItem; n: number; aids: ViewAids }) {
  const src = displaySrc(item)
  const w = item.width || 1
  const h = item.height || 1
  const cells = tileCells(n, aids.tileMode)
  return (
    <div
      className="va-tiles"
      role="img"
      aria-label={`Repeat preview, ${n} by ${n}${aids.tileMode === 'straight' ? '' : `, ${aids.tileMode}`}`}
      style={{ aspectRatio: `${w} / ${h}`, ['--va-aspect' as string]: String(w / h) }}
    >
      {cells.map((c) => (
        <span
          key={`${c.x}:${c.y}`}
          className="va-tile"
          // 1 px of overlap: sub-pixel edges would otherwise show as false seams
          style={{ left: `${(c.x / n) * 100}%`, top: `${(c.y / n) * 100}%`, width: `calc(${100 / n}% + 1px)`, height: `calc(${100 / n}% + 1px)`, backgroundImage: `url("${src}")` }}
        />
      ))}
    </div>
  )
}

const BROWSER_SHOWS = /^(png|jpe?g|gif|webp|avif|bmp)$/i

/** 1 image pixel = 1 device pixel, panned by dragging or scrolling. */
function ActualView({ item, aids }: { item: FrameItem; aids: ViewAids }) {
  const box = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; sl: number; st: number } | null>(null)
  const [dpr, setDpr] = useState(() => window.devicePixelRatio || 1)
  useEffect(() => {
    // the ratio changes with browser zoom or moving to another screen
    const mq = window.matchMedia(`(resolution: ${dpr}dppx)`)
    const on = () => setDpr(window.devicePixelRatio || 1)
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [dpr])
  const natW = item.width ?? 0
  const natH = item.height ?? 0
  const heic = /^hei[cf]$/i.test(item.ext)
  const src = originalSrc(item)
  const shown = actualSource(natW, natH, item.kind === 'image' && BROWSER_SHOWS.test(item.ext), heic ? 4096 : 2048)
  const size = actualSize(shown.w, shown.h, dpr)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2
    el.scrollTop = (el.scrollHeight - el.clientHeight) / 2
  }, [item.id, size.w, size.h])
  if (!natW || !natH) return <p className="va-actual-note">This item has no pixel size to show at 100 %.</p>
  return (
    <div className="va-actual">
      <div
        ref={box}
        className="va-actual-scroll"
        tabIndex={0}
        aria-label="Image at 100 %: drag or scroll to move around"
        onPointerDown={(e) => {
          if (e.pointerType !== 'mouse' || !box.current) return
          drag.current = { x: e.clientX, y: e.clientY, sl: box.current.scrollLeft, st: box.current.scrollTop }
          e.currentTarget.setPointerCapture(e.pointerId)
        }}
        onPointerMove={(e) => {
          const d = drag.current
          if (!d || !box.current) return
          box.current.scrollLeft = d.sl - (e.clientX - d.x)
          box.current.scrollTop = d.st - (e.clientY - d.y)
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        <div className="va-actual-pad">
          <div className="va-actual-pic">
            <img className="va-actual-img" src={src} alt={`${item.filename ?? 'The image'} at 100 %`} draggable={false} style={{ width: size.w, height: size.h }} />
            <ViewAidsGrid aids={aids} measureKey={`${item.id}:actual:${size.w}`} />
          </div>
        </div>
      </div>
      <span className="va-actual-badge" role="status">
        100 % · {shown.original ? `${shown.w} × ${shown.h} px` : `largest preview, ${shown.w} × ${shown.h} px (the browser can’t draw the original)`}
        {dpr !== 1 ? ` · ${dpr}× screen` : ''}
      </span>
    </div>
  )
}

/** What is on, over the picture (view only: the file isn't changed). */
export function ViewAidsBadge({ aids, features = ALL_FEATURES, onClear }: { aids: ViewAids; features?: AidFeatures; onClear: () => void }) {
  const on = aidsSummary(aids, features).filter((s) => s !== '100 %')
  if (!on.length) return null
  return (
    <div className="va-badge" role="status" onClick={(e) => e.stopPropagation()}>
      <span>{on.join(' · ')}</span>
      <button type="button" className="va-badge-clear" aria-label="Turn the viewing aids off" title="Turn the viewing aids off" onClick={onClear}>
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  )
}

/** The View button and its menu. */
export function ViewAidsMenu({
  aids,
  setAids,
  features = ALL_FEATURES,
  mobile = false,
  className = '',
}: {
  aids: ViewAids
  setAids: (a: ViewAids) => void
  features?: AidFeatures
  mobile?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const uid = useId()
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', away, true)
    wrap.current?.addEventListener('keydown', esc)
    const w = wrap.current
    return () => {
      document.removeEventListener('pointerdown', away, true)
      w?.removeEventListener('keydown', esc)
    }
  }, [open])
  const set = (p: Partial<ViewAids>) => setAids({ ...aids, ...p })
  const on = anyAid(aids)
  const seg = <T extends string | number>(label: string, value: T, options: [T, string][], pick: (v: T) => void, key: string) => (
    <div className="va-row">
      <span className="va-row-label" id={`${uid}-${label}`}>
        {label} <kbd>{key}</kbd>
      </span>
      <div className="seg va-seg" role="group" aria-labelledby={`${uid}-${label}`}>
        {options.map(([v, text]) => (
          <button key={String(v)} type="button" className={value === v ? 'on' : ''} aria-pressed={value === v} onClick={() => pick(v)}>
            {text}
          </button>
        ))}
      </div>
    </div>
  )
  return (
    <div ref={wrap} className={`va-menu-wrap${mobile ? ' va-phone' : ''}${className ? ` ${className}` : ''}`} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`va-toggle btn-icon${open || on ? ' on' : ''}`}
        aria-expanded={open}
        aria-controls={open ? `${uid}-menu` : undefined}
        aria-haspopup="true"
        onClick={() => setOpen(!open)}
        title="Viewing aids: greyscale, posterise, mirror, grid, repeat, 100 %"
      >
        <Eye size={16} aria-hidden="true" />
        View{on ? <span className="va-dot" aria-label=" (on)" /> : null}
      </button>
      {open && (
        <div id={`${uid}-menu`} className="va-menu float-surface" role="group" aria-label="Viewing aids">
          <label className="va-check">
            <input type="checkbox" checked={aids.grey || aids.posterize > 0} onChange={(e) => set({ grey: e.target.checked, posterize: e.target.checked ? aids.posterize : 0 })} />
            Greyscale (value check) <kbd>G</kbd>
          </label>
          {seg('Posterise', aids.posterize, [[0, 'Off'], [3, '3'], [5, '5']], (v) => set({ posterize: v as ViewAids['posterize'] }), 'V')}
          <label className="va-check">
            <input type="checkbox" checked={aids.mirror} onChange={(e) => set({ mirror: e.target.checked })} />
            Mirror <kbd>H</kbd>
          </label>
          {seg('Grid', aids.grid, [['off', 'Off'], ['thirds', 'Thirds'], ['4x4', '4 × 4'], ['custom', 'Custom']], (v) => set({ grid: v as ViewAids['grid'] }), 'L')}
          {aids.grid === 'custom' && (
            <label className="va-row va-gridn">
              <span className="va-row-label">Lines per side</span>
              <input
                type="number"
                min={GRID_N_MIN}
                max={GRID_N_MAX}
                value={aids.gridN}
                onChange={(e) => set({ gridN: clampGridN(Number(e.target.value)) })}
              />
            </label>
          )}
          {features.tile && seg('Repeat', aids.tile, [[0, 'Off'], [2, '2 × 2'], [3, '3 × 3']], (v) => set({ tile: v as ViewAids['tile'], actual: false }), 'T')}
          {features.tile &&
            aids.tile > 0 &&
            seg('Offset', aids.tileMode, [['straight', 'Straight'], ['half-drop', 'Half-drop'], ['half-brick', 'Half-brick']], (v) => set({ tileMode: v as ViewAids['tileMode'] }), 'T')}
          {features.actual && (
            <label className="va-check">
              <input type="checkbox" checked={aids.actual} onChange={(e) => set({ actual: e.target.checked, tile: 0 })} />
              True 100 % (1 image px = 1 screen px) <kbd>1</kbd>
            </label>
          )}
          <div className="va-foot">
            <span className="hint">View only: the file isn’t changed.</span>
            <button type="button" disabled={!on} onClick={() => setAids({ ...DEFAULT_AIDS, gridN: aids.gridN, tileMode: aids.tileMode })}>
              Reset
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
