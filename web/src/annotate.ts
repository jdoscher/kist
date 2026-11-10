// Annotation geometry (Milestone 3), no React (tests/annotate.test.ts).
// Points and boxes are fractions 0-1 of the image; time ranges are seconds.

export type AnnotationKind = 'point' | 'box' | 'range'

export interface Annotation {
  id: string
  item_id: string
  kind: AnnotationKind
  x: number | null
  y: number | null
  w: number | null
  h: number | null
  t_start: number | null
  t_end: number | null
  text: string
  author: string
  created_at: string
  resolved: boolean
}

export type AnnotationIn = Partial<Omit<Annotation, 'id' | 'item_id' | 'author' | 'created_at'>> & { kind: AnnotationKind }

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/** A pointer position over the shown image as fractions of it. */
export function toFraction(
  rect: { left: number; top: number; width: number; height: number },
  clientX: number,
  clientY: number,
): { x: number; y: number } {
  return {
    x: clamp01(rect.width ? (clientX - rect.left) / rect.width : 0),
    y: clamp01(rect.height ? (clientY - rect.top) / rect.height : 0),
  }
}

/** A box dragged from a to b (any direction), or null when it's too small
 * to mean a box (then it's a point). */
export function boxFrom(a: { x: number; y: number }, b: { x: number; y: number }, min = 0.01) {
  const w = Math.abs(b.x - a.x)
  const h = Math.abs(b.y - a.y)
  if (w < min || h < min) return null
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w, h }
}

/** "1:05", "1:02:03", "0:07.5" for fractions when `tenths`. */
export function fmtTime(s: number | null | undefined, tenths = false): string {
  if (s == null || !Number.isFinite(s)) return ''
  const neg = s < 0
  let t = Math.abs(s)
  const h = Math.floor(t / 3600)
  t -= h * 3600
  const m = Math.floor(t / 60)
  t -= m * 60
  const sec = tenths ? t.toFixed(1).padStart(4, '0') : String(Math.floor(t)).padStart(2, '0')
  return `${neg ? '-' : ''}${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${sec}`
}

/** Where a range sits on a scrubber, as CSS percentages. */
export function rangeOnBar(t0: number, t1: number | null, duration: number): { left: string; width: string } {
  if (!duration || duration <= 0) return { left: '0%', width: '0%' }
  const a = Math.min(1, Math.max(0, t0 / duration))
  const b = Math.min(1, Math.max(a, (t1 ?? t0) / duration))
  return { left: `${(a * 100).toFixed(3)}%`, width: `${Math.max(0.5, (b - a) * 100).toFixed(3)}%` }
}

/** Annotations active at time t (a range covering it, or a mark within
 * half a second), for the "notes now" list under a video. */
export function activeAt(list: Annotation[], t: number): Annotation[] {
  return list.filter((a) => a.t_start != null && t >= a.t_start - 0.5 && t <= (a.t_end ?? a.t_start) + 0.5)
}

/** Order for the side list: unresolved first, then by time or position,
 * then oldest first. */
export function sortAnnotations(list: Annotation[]): Annotation[] {
  return [...list].sort(
    (a, b) =>
      Number(a.resolved) - Number(b.resolved) ||
      (a.t_start ?? -1) - (b.t_start ?? -1) ||
      (a.y ?? 0) - (b.y ?? 0) ||
      (a.x ?? 0) - (b.x ?? 0) ||
      a.created_at.localeCompare(b.created_at),
  )
}

/** Short description for screen readers and the list. */
export function annotationWhere(a: Annotation): string {
  if (a.kind === 'range') return a.t_end != null && a.t_end !== a.t_start ? `${fmtTime(a.t_start)}–${fmtTime(a.t_end)}` : `at ${fmtTime(a.t_start)}`
  const pct = (v: number | null) => `${Math.round((v ?? 0) * 100)}%`
  if (a.kind === 'box') return `box at ${pct(a.x)} across, ${pct(a.y)} down`
  return `pin at ${pct(a.x)} across, ${pct(a.y)} down`
}
