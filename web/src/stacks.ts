// Stacks in the grid (no React; tests/stacks.test.ts). A list asks the
// server for one entry per stack (stacks=collapse): its cover, or in a
// ranked search the member that matched, with stack_count. Opening a stack
// puts its other members right after it, in place; closing takes them out.
// Which stacks are open is kept for this tab (a small store the grid,
// preview and info panel all read through useCurrentItems).

import type { GridItem } from './api.ts'

/** The open stacks: stack id -> its members (from GET /api/stacks/{id}). */
export type Expanded = ReadonlyMap<string, GridItem[]>

/** The list with every open stack's other members after its entry. Members
 * already in the list elsewhere aren't repeated. */
export function expandStacks(items: GridItem[], expanded: Expanded): GridItem[] {
  if (!expanded.size) return items
  const shown = new Set(items.map((i) => i.id))
  const out: GridItem[] = []
  for (const it of items) {
    out.push(it)
    const members = it.stack_id ? expanded.get(it.stack_id) : undefined
    if (!members) continue
    for (const m of members) {
      if (shown.has(m.id)) continue
      shown.add(m.id)
      out.push(m)
    }
  }
  return out
}

/** Ids shown only because their stack is open (not entries of the list). */
export function expandedMemberIds(items: GridItem[], expanded: Expanded): Set<string> {
  const base = new Set(items.map((i) => i.id))
  const out = new Set<string>()
  for (const members of expanded.values()) for (const m of members) if (!base.has(m.id)) out.add(m.id)
  return out
}

/** The stacks a selection touches (for Unstack). */
export function stacksOf(items: Pick<GridItem, 'stack_id'>[]): string[] {
  return [...new Set(items.map((i) => i.stack_id).filter((s): s is string => !!s))]
}

/** Whether the selection can become one stack: two or more items, not all
 * already in the same stack. */
export function canStack(items: Pick<GridItem, 'stack_id'>[]): boolean {
  if (items.length < 2) return false
  const s = stacksOf(items)
  return !(s.length === 1 && items.every((i) => i.stack_id === s[0]))
}

/** "3 items" badge text, and its accessible name. */
export function stackBadge(count: number | null | undefined, open: boolean): { text: string; label: string } | null {
  if (!count || count < 2) return null
  return {
    text: String(count),
    label: `${open ? 'Close' : 'Open'} stack of ${count} items`,
  }
}

// ---------- the store ----------

let state: Map<string, GridItem[]> = new Map()
const listeners = new Set<() => void>()

export function getExpanded(): Expanded {
  return state
}

export function subscribeExpanded(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function emit(next: Map<string, GridItem[]>) {
  state = next
  for (const fn of listeners) fn()
}

export function openStack(stackId: string, members: GridItem[]) {
  const next = new Map(state)
  next.set(stackId, members)
  emit(next)
}

export function closeStack(stackId: string) {
  if (!state.has(stackId)) return
  const next = new Map(state)
  next.delete(stackId)
  emit(next)
}

export function closeAllStacks() {
  if (state.size) emit(new Map())
}
