// What an open item can be compared with, without a multi-select (the
// phone has none): the previous version, the rest of its stack, or an item
// picked in ⌘K. No React (tests/compareTargets.test.ts).

/** Compare shows at most this many side by side (Survey takes more). */
export const COMPARE_MAX = 4

export interface CompareChoice {
  key: 'version' | 'stack'
  label: string
  /** what Compare opens, in this order */
  ids: string[]
}

/** "Compare with vN": the version before this one in the version_of chain
 * (oldest first), as the desktop info panel offers it. */
export function versionChoice(chain: { id: string }[], itemId: string): CompareChoice | null {
  const at = chain.findIndex((v) => v.id === itemId)
  if (at <= 0) return null
  return { key: 'version', label: `Compare with v${at}`, ids: [chain[at - 1].id, itemId] }
}

/** "Compare with stack": this item first, then the cover, then the other
 * members in stack order, up to COMPARE_MAX. Null when it's alone. */
export function stackChoice(
  stack: { cover_item_id: string; items: { id: string }[] } | null | undefined,
  itemId: string,
  max = COMPARE_MAX,
): CompareChoice | null {
  if (!stack) return null
  const members = stack.items.map((i) => i.id)
  const others = members.filter((id) => id !== itemId)
  if (!others.length) return null
  const order = [stack.cover_item_id, ...others].filter((id, i, a) => id !== itemId && others.includes(id) && a.indexOf(id) === i)
  const total = others.length + 1
  return {
    key: 'stack',
    label: total > max ? `Compare with stack (${max} of ${total})` : 'Compare with stack',
    ids: [itemId, ...order].slice(0, max),
  }
}

const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x))

/** The phone sheet's compare buttons. A two-version stack would offer the
 * same pair twice; then only "Compare with vN" shows. */
export function sheetCompareChoices(
  chain: { id: string }[] | null | undefined,
  stack: { cover_item_id: string; items: { id: string }[] } | null | undefined,
  itemId: string,
): CompareChoice[] {
  const v = versionChoice(chain ?? [], itemId)
  const s = stackChoice(stack, itemId)
  const out: CompareChoice[] = []
  if (v) out.push(v)
  if (s && !(v && sameSet(v.ids, s.ids))) out.push(s)
  return out
}
