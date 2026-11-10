// The date scrubber (M6 W1.1): desktop's right-edge rail with year ticks
// and a floating month label; on the phone a thumb that shows while the
// grid scrolls and can be dragged. Either way the control is a slider over
// the list's months (aria-valuetext "June 2024"). Data: useScrubWindow.ts.

import { useCallback, useEffect, useRef, useState } from 'react'
import { bucketAt, monthLabel, railPosition, sliderKey, yearTicks, type ScrubModel } from '../scrub'

export function Scrubber({
  model,
  scrollEl,
  positionAt,
  onScrub,
  onScrubEnd,
  mobile,
  layoutKey,
}: {
  model: ScrubModel
  scrollEl: HTMLElement
  /** list position at the grid's current scroll offset */
  positionAt: (y: number) => number
  /** scroll the grid to list position pos */
  onScrub: (pos: number) => void
  /** dragging (or a key) ended at list position pos: go there */
  onScrubEnd: (pos: number) => void
  mobile: boolean
  /** changes when the loaded window does, so the thumb re-reads its place */
  layoutKey: string
}) {
  const railRef = useRef<HTMLDivElement | null>(null)
  const [box, setBox] = useState({ top: 0, height: 0 })
  const [pos, setPos] = useState(0)
  const [drag, setDrag] = useState<number | null>(null) // 0..1 down the rail
  const [hover, setHover] = useState<number | null>(null)
  const [active, setActive] = useState(false) // phone: shown while scrolling
  const [focused, setFocused] = useState(false)
  // the month picked with the keyboard: the slider's value until the grid
  // is scrolled some other way (the list's last months can't reach the top
  // of the screen, so the scroll position alone would not say it)
  const [pinned, setPinned] = useState<number | null>(null)
  const hideTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  // the rail covers the scroll box (below the title row)
  useEffect(() => {
    const measure = () => setBox({ top: scrollEl.offsetTop, height: scrollEl.clientHeight })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(scrollEl)
    return () => ro.disconnect()
  }, [scrollEl])

  // follow the grid's scroll
  useEffect(() => {
    const at = () =>
      railPosition(
        positionAt(scrollEl.scrollTop),
        model.total,
        scrollEl.scrollTop,
        scrollEl.scrollHeight - scrollEl.clientHeight,
        scrollEl.clientHeight,
      )
    const onScroll = () => {
      setPos(at())
      if (mobile) {
        setActive(true)
        clearTimeout(hideTimer.current)
        hideTimer.current = setTimeout(() => setActive(false), 1500)
      }
    }
    setPos(at())
    const unpin = () => setPinned(null)
    scrollEl.addEventListener('scroll', onScroll, { passive: true })
    scrollEl.addEventListener('wheel', unpin, { passive: true })
    scrollEl.addEventListener('touchstart', unpin, { passive: true })
    scrollEl.addEventListener('pointerdown', unpin)
    scrollEl.addEventListener('keydown', unpin)
    return () => {
      scrollEl.removeEventListener('scroll', onScroll)
      scrollEl.removeEventListener('wheel', unpin)
      scrollEl.removeEventListener('touchstart', unpin)
      scrollEl.removeEventListener('pointerdown', unpin)
      scrollEl.removeEventListener('keydown', unpin)
      clearTimeout(hideTimer.current)
    }
  }, [scrollEl, positionAt, mobile, model.total, layoutKey])

  const total = Math.max(1, model.total)
  const fracAt = useCallback((clientY: number) => {
    const r = railRef.current?.getBoundingClientRect()
    if (!r || r.height <= 0) return 0
    return Math.min(1, Math.max(0, (clientY - r.top) / r.height))
  }, [])

  const scrubTo = (f: number) => {
    setDrag(f)
    const p = Math.min(model.total - 0.001, f * model.total)
    setPos(p)
    onScrub(p)
  }

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    e.preventDefault()
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
    setPinned(null)
    scrubTo(fracAt(e.clientY))
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (drag !== null) scrubTo(fracAt(e.clientY))
    else if (!mobile && e.pointerType === 'mouse') setHover(fracAt(e.clientY))
  }
  const endDrag = (e: React.PointerEvent) => {
    if (drag === null) return
    setDrag(null)
    // where the pointer let go (the last move may not have rendered yet)
    const f = e.type === 'pointerup' ? fracAt(e.clientY) : drag
    const p = Math.min(model.total - 0.001, f * model.total)
    // the month let go on stays the value (the row it lands on can start
    // with the month before)
    setPinned(Math.max(0, bucketAt(model, p)))
    onScrubEnd(p)
  }

  const here = drag !== null ? Math.min(model.total - 0.001, drag * model.total) : pos
  const idx = drag === null && pinned !== null && pinned < model.buckets.length ? pinned : Math.max(0, bucketAt(model, here))
  const label = monthLabel(model.buckets[idx]?.key ?? null)

  const onKeyDown = (e: React.KeyboardEvent) => {
    const j = sliderKey(model, idx, e.key)
    if (j === null) return
    e.preventDefault()
    e.stopPropagation()
    const p = model.starts[j]
    setPinned(j)
    setPos(p)
    onScrubEnd(p)
  }

  // the rail also scrolls the grid under a wheel
  const onWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) return
    scrollEl.scrollTop += e.deltaY
  }

  const thumbAt = (drag ?? pos / total) * 100
  const ticks = yearTicks(model, box.height > 0 ? 18 / box.height : 0)
  const showLabel = drag !== null || (focused && !mobile) || (hover !== null && !mobile)
  const labelAt = drag !== null ? drag : hover !== null ? hover : pos / total
  const labelText =
    drag === null && hover !== null ? monthLabel(model.buckets[bucketAt(model, hover * model.total)]?.key ?? null) : label
  const shown = !mobile || active || drag !== null || focused

  return (
    <div
      className={`scrubber${mobile ? ' scrubber-phone' : ''}${shown ? ' shown' : ''}${drag !== null ? ' dragging' : ''}`}
      style={{ top: box.top, height: box.height }}
    >
      <div
        ref={railRef}
        className="scrub-track"
        role={mobile ? undefined : 'slider'}
        tabIndex={mobile ? undefined : 0}
        aria-label={mobile ? undefined : 'Jump to date'}
        aria-orientation={mobile ? undefined : 'vertical'}
        aria-valuemin={mobile ? undefined : 0}
        aria-valuemax={mobile ? undefined : model.buckets.length - 1}
        aria-valuenow={mobile ? undefined : idx}
        aria-valuetext={mobile ? undefined : label}
        onKeyDown={mobile ? undefined : onKeyDown}
        onFocus={mobile ? undefined : () => setFocused(true)}
        onBlur={
          mobile
            ? undefined
            : () => {
                setFocused(false)
                setPinned(null)
              }
        }
        onPointerDown={mobile ? undefined : onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={() => setHover(null)}
        onWheel={onWheel}
      >
        {(!mobile || drag !== null) &&
          ticks.map((t) => (
            <span key={t.bucket} className="scrub-year" style={{ top: `${t.at * 100}%` }} aria-hidden="true">
              {t.label}
            </span>
          ))}
        {!mobile && <span className="scrub-mark" style={{ top: `${thumbAt}%` }} aria-hidden="true" />}
        {mobile && (
          <div
            className="scrub-thumb"
            style={{ top: `${thumbAt}%` }}
            role="slider"
            tabIndex={0}
            aria-label="Jump to date"
            aria-orientation="vertical"
            aria-valuemin={0}
            aria-valuemax={model.buckets.length - 1}
            aria-valuenow={idx}
            aria-valuetext={label}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => {
              setFocused(false)
              setPinned(null)
            }}
            onPointerDown={onPointerDown}
          >
            <span aria-hidden="true" className="scrub-grip" />
          </div>
        )}
        {showLabel && (
          <span className="scrub-label" style={{ top: `${labelAt * 100}%` }} aria-hidden="true">
            {labelText}
          </span>
        )}
      </div>
    </div>
  )
}
