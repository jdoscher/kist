// Pick/reject flags and colour labels (Milestone 3), no React
// (tests/cull.test.ts). Lightroom's keys: P pick, X reject, U unflag,
// 6-9 the first four labels (0-5 are already the rating).

export type Flag = 'pick' | 'reject'
export type Label = 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple'

export const LABELS: { id: Label; name: string; hex: string }[] = [
  { id: 'red', name: 'Red', hex: '#e5484d' },
  { id: 'orange', name: 'Orange', hex: '#f76b15' },
  { id: 'yellow', name: 'Yellow', hex: '#f5c518' },
  { id: 'green', name: 'Green', hex: '#30a46c' },
  { id: 'blue', name: 'Blue', hex: '#3e8ef7' },
  { id: 'purple', name: 'Purple', hex: '#8e4ec6' },
]

export function labelHex(l: string | null | undefined): string | null {
  return LABELS.find((x) => x.id === l)?.hex ?? null
}

export function labelName(l: string | null | undefined): string {
  return LABELS.find((x) => x.id === l)?.name ?? 'No label'
}

export function isFlag(v: unknown): v is Flag {
  return v === 'pick' || v === 'reject'
}

export function isLabel(v: unknown): v is Label {
  return LABELS.some((l) => l.id === v)
}

export function flagName(f: string | null | undefined): string {
  return f === 'pick' ? 'Pick' : f === 'reject' ? 'Reject' : 'Unflagged'
}

/** What a culling key does. Keys typed with ⌘/Ctrl/Alt are never ours. */
export type CullAction =
  | { kind: 'flag'; flag: Flag | null }
  | { kind: 'rate'; rating: number }
  | { kind: 'label'; label: Label }
  | { kind: 'tag' }
  | { kind: 'folder' }
  | { kind: 'next' }
  | { kind: 'prev' }
  | { kind: 'undo' }
  | { kind: 'trash' }

/** The triage keys: P/X/U flag, 0-5 rate (0 clears), 6-9 label (pressing
 * the item's own label again clears it: the caller decides), T tag box,
 * F folder picker, →/← next/previous, ⌘Z undo the last change, Delete
 * trashes. */
export function triageKey(e: { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey?: boolean }): CullAction | null {
  if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'z') return { kind: 'undo' }
  if (e.metaKey || e.ctrlKey || e.altKey) return null
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key
  if (k === 'p') return { kind: 'flag', flag: 'pick' }
  if (k === 'x') return { kind: 'flag', flag: 'reject' }
  if (k === 'u') return { kind: 'flag', flag: null }
  if (k >= '0' && k <= '5' && k.length === 1) return { kind: 'rate', rating: Number(k) }
  if (k >= '6' && k <= '9' && k.length === 1) return { kind: 'label', label: LABELS[Number(k) - 6].id }
  if (k === 't') return { kind: 'tag' }
  if (k === 'f') return { kind: 'folder' }
  if (k === 'ArrowRight' || k === 'j') return { kind: 'next' }
  if (k === 'ArrowLeft' || k === 'k') return { kind: 'prev' }
  if (k === 'Delete' || k === 'Backspace') return { kind: 'trash' }
  return null
}

/** Whether an action moves on to the next item when auto-advance is on
 * (a decision was made: a flag, a rating or a label). */
export function advances(a: CullAction): boolean {
  return a.kind === 'flag' || a.kind === 'rate' || a.kind === 'label'
}

/** Phone triage: swipe up keeps (pick), down rejects, left/right move. */
export function swipeAction(dir: 'left' | 'right' | 'up' | 'down' | null): CullAction | null {
  if (dir === 'up') return { kind: 'flag', flag: 'pick' }
  if (dir === 'down') return { kind: 'flag', flag: 'reject' }
  if (dir === 'left') return { kind: 'next' }
  if (dir === 'right') return { kind: 'prev' }
  return null
}

/** Survey: after deciding on one, the next undecided after it (wrapping),
 * or null when every one has a flag. */
export function nextUndecided(ids: string[], flags: Record<string, string | null | undefined>, from: string | null): string | null {
  if (!ids.length) return null
  const start = from ? ids.indexOf(from) : -1
  for (let i = 1; i <= ids.length; i++) {
    const id = ids[(start + i + ids.length) % ids.length]
    if (!flags[id]) return id
  }
  return null
}
