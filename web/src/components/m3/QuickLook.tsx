// Quick look: hold Space on a grid tile to peek at it big (let go to
// close; a quick tap still selects, as before). While peeking, Z latches
// it open with a 100% loupe that follows the pointer (Space or Escape then
// closes). The <Loupe> also works in the full-screen preview (Z).

import { useEffect, useRef, useState } from 'react'
import type { Item } from '../../api'
import { useCurrentItems, useItem } from '../../data'
import { displaySrc, loupeStyle, originalSrc } from '../../look'
import { shortcuts } from '../../shortcuts'
import { useAppState } from '../../state'
import { tileDomId } from '../Grid'

const HOLD_MS = 220
const LENS = 220

const tileIdOf = (el: Element | null): string | null => {
  const t = el?.closest?.('.grid-scroll [role=option][id^="tile-"]')
  return t ? t.id.slice('tile-'.length) : null
}

export function QuickLook() {
  const { toggleSelect, selectOnly, screen, settings, previewId } = useAppState()
  const { items } = useCurrentItems()
  const [peekId, setPeekId] = useState<string | null>(null)
  const [latched, setLatched] = useState(false)
  const [loupe, setLoupe] = useState(false)
  const hovered = useRef<string | null>(null)
  const press = useRef<{ id: string; timer: ReturnType<typeof setTimeout>; fromTile: boolean } | null>(null)
  const state = useRef({ peekId, latched, loupe })
  state.current = { peekId, latched, loupe }
  const enabled = !screen && !settings && !previewId
  const item = useItem(peekId, items)

  useEffect(() => {
    const over = (e: PointerEvent) => {
      hovered.current = tileIdOf(e.target as Element)
    }
    document.addEventListener('pointerover', over)
    return () => document.removeEventListener('pointerover', over)
  }, [])

  useEffect(() => {
    if (!enabled) return
    const close = () => {
      setPeekId(null)
      setLatched(false)
      setLoupe(false)
    }
    const down = (e: KeyboardEvent) => {
      const s = state.current
      if (s.peekId) {
        if (e.key === 'z' || e.key === 'Z') {
          e.preventDefault()
          e.stopPropagation()
          setLoupe(!s.loupe)
          setLatched(true)
          return
        }
        if (e.key === 'Escape' || (e.key === ' ' && s.latched && !e.repeat)) {
          e.preventDefault()
          e.stopPropagation()
          close()
          return
        }
        if (e.key === ' ') {
          e.preventDefault()
          e.stopPropagation()
        }
        return
      }
      if (e.key !== ' ' || e.metaKey || e.ctrlKey || e.altKey || shortcuts.topLayer() !== 'grid') return
      const t = e.target as HTMLElement
      const onTile = tileIdOf(t)
      const id = onTile ?? (t === document.body ? hovered.current : null)
      if (!id) return
      e.preventDefault()
      e.stopPropagation()
      if (e.repeat || press.current) return
      press.current = {
        id,
        fromTile: !!onTile,
        timer: setTimeout(() => {
          setPeekId(id)
        }, HOLD_MS),
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.key !== ' ') return
      const p = press.current
      press.current = null
      if (p) clearTimeout(p.timer)
      const s = state.current
      if (s.peekId) {
        if (!s.latched) close()
        e.preventDefault()
        return
      }
      if (!p) return
      // a tap: what Space did before (select, or toggle on a focused tile)
      if (p.fromTile) toggleSelect(p.id)
      else {
        selectOnly(p.id)
        document.getElementById(tileDomId(p.id))?.focus({ preventScroll: true })
      }
    }
    window.addEventListener('keydown', down, true)
    window.addEventListener('keyup', up, true)
    return () => {
      window.removeEventListener('keydown', down, true)
      window.removeEventListener('keyup', up, true)
    }
  }, [enabled, toggleSelect, selectOnly])

  useEffect(() => {
    if (!enabled) {
      setPeekId(null)
      setLatched(false)
      setLoupe(false)
    }
  }, [enabled])

  if (!peekId || !item) return null
  return (
    <div className="quicklook" role="dialog" aria-label={`Quick look: ${item.filename}`} aria-live="polite">
      <figure>
        <PeekImage item={item} loupe={loupe} />
        <figcaption>
          <span className="fname">{item.filename}</span>
          <span className="meta">{item.width && item.height ? `${item.width}×${item.height}` : ''}</span>
          <span className="ql-hint">{latched ? 'Space or Esc closes · Z loupe' : 'Let go of Space to close · Z for a 100% loupe'}</span>
        </figcaption>
      </figure>
    </div>
  )
}

function PeekImage({ item, loupe }: { item: Item; loupe: boolean }) {
  const [src, setSrc] = useState(`/api/items/${item.id}/thumb?size=1024&v=${item.preview_rev}`)
  useEffect(() => {
    if (item.kind === 'video') return
    const want = displaySrc(item)
    const img = new Image()
    img.onload = () => setSrc(want)
    img.src = want
    return () => {
      img.onload = null
    }
  }, [item])
  if (item.kind === 'video')
    return <video src={`/api/items/${item.id}/file`} autoPlay muted loop playsInline aria-label={item.filename} />
  if (!item.has_thumb && item.kind !== 'image') return <div className="preview-fallback">No preview for .{item.ext}</div>
  return (
    <Loupe item={item} on={loupe}>
      <img src={item.has_thumb ? src : originalSrc(item)} alt={item.filename} draggable={false} />
    </Loupe>
  )
}

/** Wrap an <img>: with `on`, a round lens follows the pointer showing the
 * image at 100% (the display copy first, then the original). */
export function Loupe({ item, on, children }: { item: Pick<Item, 'id' | 'ext' | 'kind' | 'width' | 'height' | 'has_thumb' | 'preview_rev'>; on: boolean; children: React.ReactNode }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ x: number; y: number; fx: number; fy: number } | null>(null)
  const [bg, setBg] = useState<string>(displaySrc(item))
  useEffect(() => {
    if (!on) return
    setBg(displaySrc(item))
    const want = originalSrc(item)
    const img = new Image()
    img.onload = () => setBg(want)
    img.src = want
    return () => {
      img.onload = null
    }
  }, [on, item])
  const natW = item.width ?? 0
  const natH = item.height ?? 0
  return (
    <div
      ref={wrap}
      className={`loupe-wrap${on ? ' on' : ''}`}
      onPointerMove={(e) => {
        if (!on) return
        const img = wrap.current?.querySelector('img')
        if (!img) return
        const r = img.getBoundingClientRect()
        const w = wrap.current!.getBoundingClientRect()
        setPos({ x: e.clientX - w.left, y: e.clientY - w.top, fx: (e.clientX - r.left) / r.width, fy: (e.clientY - r.top) / r.height })
      }}
      onPointerLeave={() => setPos(null)}
    >
      {children}
      {on && pos && natW > 0 && (
        <div
          className="loupe"
          aria-hidden="true"
          style={{
            left: pos.x - LENS / 2,
            top: pos.y - LENS / 2,
            width: LENS,
            height: LENS,
            backgroundImage: `url("${bg}")`,
            ...loupeStyle(natW, natH, pos.fx, pos.fy, LENS),
          }}
        />
      )}
      {on && <span className="loupe-badge">100% loupe</span>}
    </div>
  )
}
