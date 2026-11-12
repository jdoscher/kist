// Crop an item node: drag a frame over the image (move it, or its corners),
// or type the numbers. The original is never touched; the board keeps the
// crop as fractions.

import { useRef, useState } from 'react'
import { thumbUrl, type Item } from '../../../api'
import { clampCrop, type Crop } from '../../../board'
import { Modal } from '../../Dialog'

type Drag = { mode: 'move' | 'nw' | 'ne' | 'sw' | 'se' | 'new'; x: number; y: number; start: Crop }

export function CropDialog({ item, crop, onApply, onClose }: { item: Item; crop: Crop | null; onApply: (c: Crop) => void; onClose: () => void }) {
  const [c, setC] = useState<Crop>(crop ?? { x: 0.1, y: 0.1, w: 0.8, h: 0.8 })
  const box = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const frac = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect()
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }
  }
  const onDown = (e: React.PointerEvent) => {
    const t = e.target as HTMLElement
    const p = frac(e)
    const mode = (t.dataset.corner as Drag['mode']) ?? (t.classList.contains('crop-frame') ? 'move' : 'new')
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { mode, x: p.x, y: p.y, start: mode === 'new' ? { x: p.x, y: p.y, w: 0.02, h: 0.02 } : c }
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const p = frac(e)
    const dx = p.x - d.x
    const dy = p.y - d.y
    const s = d.start
    let n: Crop
    if (d.mode === 'move') n = { ...s, x: s.x + dx, y: s.y + dy }
    else if (d.mode === 'new') n = { x: Math.min(d.x, p.x), y: Math.min(d.y, p.y), w: Math.abs(dx), h: Math.abs(dy) }
    else {
      const left = d.mode.includes('w')
      const top = d.mode.includes('n')
      const x0 = left ? s.x + dx : s.x
      const y0 = top ? s.y + dy : s.y
      const x1 = left ? s.x + s.w : s.x + s.w + dx
      const y1 = top ? s.y + s.h : s.y + s.h + dy
      n = { x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) }
    }
    setC(clampCrop(n))
  }
  const pct = (v: number) => Math.round(v * 1000) / 10
  const field = (key: keyof Crop, label: string) => (
    <label className="insp-num">
      <span>{label} %</span>
      <input
        type="number"
        step={0.5}
        min={0}
        max={100}
        value={pct(c[key])}
        onChange={(e) => setC(clampCrop({ ...c, [key]: Number(e.target.value) / 100 }))}
      />
    </label>
  )
  const src = thumbUrl(item, 1024) ?? `/api/items/${item.id}/file`
  return (
    <Modal title={`Crop ${item.filename}`} onClose={onClose} className="dialog crop-dialog">
      <div className="crop-stage" ref={box} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={() => (drag.current = null)}>
        <img src={src} alt={item.filename} draggable={false} />
        <div className="crop-shade" style={{ clipPath: `polygon(0 0, 100% 0, 100% 100%, 0 100%, 0 ${c.y * 100}%, ${c.x * 100}% ${c.y * 100}%, ${c.x * 100}% ${(c.y + c.h) * 100}%, ${(c.x + c.w) * 100}% ${(c.y + c.h) * 100}%, ${(c.x + c.w) * 100}% ${c.y * 100}%, 0 ${c.y * 100}%)` }} aria-hidden="true" />
        <div className="crop-frame" style={{ left: `${c.x * 100}%`, top: `${c.y * 100}%`, width: `${c.w * 100}%`, height: `${c.h * 100}%` }} aria-hidden="true">
          {(['nw', 'ne', 'sw', 'se'] as const).map((k) => (
            <span key={k} className={`crop-corner ${k}`} data-corner={k} />
          ))}
        </div>
      </div>
      <div className="insp-grid" role="group" aria-label="Crop, in percent of the image">
        {field('x', 'Left')}
        {field('y', 'Top')}
        {field('w', 'Width')}
        {field('h', 'Height')}
      </div>
      <div className="dialog-actions">
        <button onClick={() => setC({ x: 0, y: 0, w: 1, h: 1 })}>Whole image</button>
        <button onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={() => onApply(c)}>
          Crop
        </button>
      </div>
    </Modal>
  )
}
