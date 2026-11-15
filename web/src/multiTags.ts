// The info panel's tag editor for a multi-selection (pure, so it can be
// tested): every tag on any selected item, how many carry it, and which
// items a change touches. The component is components/MultiTags.tsx.

export interface TaggedLike {
  id: string
  tags: string[]
}

export interface TagTally {
  tag: string
  /** how many of the selected items carry it */
  count: number
  /** those items, in selection order: removing it touches only these */
  ids: string[]
}

/** Every tag on any of the items, most-shared first, then by name. */
export function tagTally(items: TaggedLike[]): TagTally[] {
  const by = new Map<string, TagTally>()
  for (const it of items) {
    for (const tag of new Set(it.tags)) {
      const row = by.get(tag) ?? { tag, count: 0, ids: [] }
      row.count += 1
      row.ids.push(it.id)
      by.set(tag, row)
    }
  }
  return [...by.values()].sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
}

/** "3/5" on a tag only some of the items carry; null when all of them do. */
export function shareText(count: number, total: number): string | null {
  return count < total ? `${count}/${total}` : null
}

/** The chip's ✕ label: says how many items lose the tag. */
export function removeLabel(t: TagTally, total: number): string {
  if (t.count >= total) return `Remove tag ${t.tag} from all ${total} items`
  return `Remove tag ${t.tag} from the ${t.count} of ${total} items that have it`
}

/** The items that miss at least one of the tags being added (comparing
 * names case-insensitively, like the server); the rest need no request. */
export function addTargets(items: TaggedLike[], tags: string[]): string[] {
  const want = tags.map((t) => t.toLowerCase())
  return items
    .filter((it) => {
      const have = new Set(it.tags.map((t) => t.toLowerCase()))
      return want.some((t) => !have.has(t))
    })
    .map((it) => it.id)
}

/** Tags every item already has: not worth suggesting again. */
export function sharedByAll(tally: TagTally[], total: number): string[] {
  return tally.filter((t) => t.count >= total).map((t) => t.tag)
}
