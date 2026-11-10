// Phone editing in the item sheet and the full-screen preview (Kist 2.0
// Milestone 6, W2.1): tag autocomplete, the Move to… sheet's folder list
// and recent folders, and the folder change a move sends. No React;
// tests/phoneEdit.test.ts.

export interface TagLike {
  name: string
  count: number
}

export interface FolderLike {
  id: string
  name: string
  parent_id: string | null
  position?: number
}

/** The tags typed into the add field: comma-separated, trimmed, no repeats. */
export function parseTagInput(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(',')) {
    const t = raw.trim()
    if (t && !out.some((o) => o.toLowerCase() === t.toLowerCase())) out.push(t)
  }
  return out
}

/** The part of the field being typed (after the last comma). */
export function currentFragment(text: string): string {
  const i = text.lastIndexOf(',')
  return (i < 0 ? text : text.slice(i + 1)).trim()
}

/** Replace the fragment being typed with a picked tag. */
export function completeFragment(text: string, tag: string): string {
  const i = text.lastIndexOf(',')
  const head = i < 0 ? '' : `${text.slice(0, i + 1)} `
  return `${head}${tag}`
}

/**
 * Up to `n` existing tags for what's being typed: names that start with it
 * first, then names that contain it (a namespace part counts as a start:
 * "sky" finds "place:sky"), each group by use. Tags the item already has,
 * and ones already typed earlier in the field, are left out.
 */
export function tagSuggestions(all: TagLike[], text: string, existing: string[], n = 6): string[] {
  const frag = currentFragment(text).toLowerCase()
  if (!frag) return []
  const skip = new Set([...existing, ...parseTagInput(text).slice(0, -1)].map((t) => t.toLowerCase()))
  const starts: TagLike[] = []
  const contains: TagLike[] = []
  for (const t of all) {
    const name = t.name.toLowerCase()
    if (skip.has(name) || name === frag) continue
    const local = name.includes(':') ? name.slice(name.indexOf(':') + 1) : name
    if (name.startsWith(frag) || local.startsWith(frag)) starts.push(t)
    else if (name.includes(frag)) contains.push(t)
  }
  const byUse = (a: TagLike, b: TagLike) => b.count - a.count || a.name.localeCompare(b.name)
  return [...starts.sort(byUse), ...contains.sort(byUse)].slice(0, n).map((t) => t.name)
}

export type MoveMode = 'move' | 'also'

/** What PATCH /api/items/{id}/folders sends for Move (the target becomes
 * the only folder) or Also in (one more folder). Null: nothing to change. */
export function folderChange(
  mode: MoveMode,
  current: string[],
  target: string,
): { add: string[]; remove: string[] } | null {
  const has = current.includes(target)
  if (mode === 'also') return has ? null : { add: [target], remove: [] }
  const remove = current.filter((f) => f !== target)
  if (has && remove.length === 0) return null
  return { add: has ? [] : [target], remove }
}

/** The notice after a move. */
export function moveDoneText(mode: MoveMode, folderName: string, removedCount: number): string {
  if (mode === 'also') return `Also in “${folderName}”.`
  return removedCount > 0 ? `Moved to “${folderName}”.` : `Filed in “${folderName}”.`
}

/** Shared with the /add card (AddPage.tsx): the last folders something was
 * filed into, newest first. */
export const RECENT_FOLDERS_KEY = 'kist_add_recent_folders'
export const RECENT_MAX = 5

export function parseRecent(raw: string | null, knownIds?: Set<string>): string[] {
  try {
    const v = JSON.parse(raw ?? '[]')
    if (!Array.isArray(v)) return []
    return v
      .filter((x): x is string => typeof x === 'string' && (!knownIds || knownIds.has(x)))
      .slice(0, RECENT_MAX)
  } catch {
    return []
  }
}

export function pushRecent(list: string[], id: string, max = RECENT_MAX): string[] {
  return [id, ...list.filter((x) => x !== id)].slice(0, max)
}

export interface FolderRow {
  id: string
  name: string
  depth: number
  /** "Parent / Child" for a search hit, so nested names can be told apart */
  path: string
}

/** The folder tree in sidebar order (position, then name) as indented rows;
 * with a search, the matching folders only, each with its path. */
export function folderRows(folders: FolderLike[], search = ''): FolderRow[] {
  const byParent = new Map<string | null, FolderLike[]>()
  const ids = new Set(folders.map((f) => f.id))
  for (const f of folders) {
    // a folder whose parent isn't in the list shows at the top level
    const p = f.parent_id && ids.has(f.parent_id) ? f.parent_id : null
    const list = byParent.get(p) ?? []
    list.push(f)
    byParent.set(p, list)
  }
  const order = (a: FolderLike, b: FolderLike) => (a.position ?? 0) - (b.position ?? 0) || a.name.localeCompare(b.name)
  const rows: FolderRow[] = []
  const seen = new Set<string>()
  const walk = (parent: string | null, depth: number, prefix: string) => {
    for (const f of (byParent.get(parent) ?? []).sort(order)) {
      if (seen.has(f.id)) continue // a cycle in bad data must not hang the sheet
      seen.add(f.id)
      const path = prefix ? `${prefix} / ${f.name}` : f.name
      rows.push({ id: f.id, name: f.name, depth, path })
      walk(f.id, depth + 1, path)
    }
  }
  walk(null, 0, '')
  const q = search.trim().toLowerCase()
  if (!q) return rows
  return rows.filter((r) => r.name.toLowerCase().includes(q)).map((r) => ({ ...r, depth: 0 }))
}
