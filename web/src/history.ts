// Item history and undo, in plain words (no React; tests/history.test.ts).
//
// The server records one event per item per change (kist/services/events.py):
// who (`actor`: a token's name, or owner / cli / worker / importer), from
// where (`source`: web, mcp, api, cli, worker, import), in which batch, and
// before/after snapshots of what changed. These functions turn that into
// sentences such as "Claude added tags #a #b" and "Claude via MCP tagged 12
// items".

import type { Batch, ItemEvent, LinkKind, UndoResult } from './api.ts'

export interface Names {
  /** folder id -> name */
  folder?: (id: string) => string | undefined
  /** item id -> filename (for links) */
  item?: (id: string) => string | undefined
}

const SOURCE_TEXT: Record<string, string> = {
  web: 'in the web app',
  mcp: 'via MCP',
  api: 'via the API',
  cli: 'from the command line',
  worker: 'automatically',
  import: 'from the inbox',
}

/** Who made a change: the owner is "You"; the system actors get names. */
export function actorName(actor: string): string {
  switch (actor) {
    case 'owner':
      return 'You'
    case 'cli':
      return 'The command line'
    case 'worker':
      return 'Kist'
    case 'importer':
      return 'The inbox importer'
    default:
      return actor || 'Someone'
  }
}

/** "via MCP", "in the web app"... ('' when it adds nothing). */
export function sourceText(source: string, actor = ''): string {
  // "The command line from the command line" says nothing twice
  if ((actor === 'cli' && source === 'cli') || (actor === 'worker' && source === 'worker')) return ''
  if (actor === 'importer' && source === 'import') return ''
  return SOURCE_TEXT[source] ?? (source ? `via ${source}` : '')
}

/** "Claude via MCP", "You in the web app", "Kist". */
export function whoText(actor: string, source: string): string {
  const src = sourceText(source, actor)
  return src ? `${actorName(actor)} ${src}` : actorName(actor)
}

const tagList = (tags: string[]) => tags.map((t) => `#${t}`).join(' ')

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

function lowerDiff(a: string[], b: string[]): string[] {
  const have = new Set(b.map((t) => t.toLowerCase()))
  return a.filter((t) => !have.has(t.toLowerCase()))
}

function folderNames(ids: string[], names: Names): string {
  const list = ids.map((id) => {
    const n = names.folder?.(id)
    return n ? `“${n}”` : 'a folder that no longer exists'
  })
  if (list.length <= 1) return list.join('')
  return `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}`
}

const FIELD_NAME: Record<string, string> = {
  filename: 'the name',
  rating: 'the rating',
  notes: 'the notes',
  source_url: 'the source',
  created_at: 'the date added',
}

function editText(before: Record<string, unknown>, after: Record<string, unknown>): string {
  const parts: string[] = []
  for (const f of Object.keys(after).length ? Object.keys(after) : Object.keys(before)) {
    const a = after[f]
    const b = before[f]
    if (f === 'filename') parts.push(`renamed it from “${String(b ?? '')}” to “${String(a ?? '')}”`)
    else if (f === 'rating') {
      const n = Number(a) || 0
      parts.push(n ? `rated it ${n} star${n === 1 ? '' : 's'}` : 'cleared the rating')
    } else if (f === 'notes') parts.push(a ? (b ? 'edited the notes' : 'added notes') : 'cleared the notes')
    else if (f === 'source_url') parts.push(a ? 'changed the source' : 'removed the source')
    else parts.push(`changed ${FIELD_NAME[f] ?? f}`)
  }
  return parts.join(', ') || 'edited it'
}

export const LINK_KINDS: { value: LinkKind; label: string; hint: string }[] = [
  { value: 'derived_from', label: 'Derived from', hint: 'this was made from the other (a crop, an edit, a screenshot of it)' },
  { value: 'alternate', label: 'Alternate', hint: 'another take or format of the same thing' },
  { value: 'related', label: 'Related', hint: 'belongs together' },
  { value: 'not_duplicate', label: 'Not a duplicate', hint: 'looks alike but isn’t the same; stops them being paired as near-duplicates' },
  { value: 'version_of', label: 'Version of', hint: 'a newer or older version of the other' },
]

/** How a link reads from this item's side: an outgoing derived_from is
 * "Derived from", the same link seen from the other item is "Source of". */
export function linkLabel(kind: string, direction: 'out' | 'in'): string {
  switch (kind) {
    case 'derived_from':
      return direction === 'out' ? 'Derived from' : 'Source of'
    case 'version_of':
      return direction === 'out' ? 'Version of' : 'Has version'
    case 'alternate':
      return 'Alternate'
    case 'related':
      return 'Related'
    case 'not_duplicate':
      return 'Not a duplicate of'
    default:
      return kind
  }
}

function linkText(link: unknown, names: Names): string {
  const l = (link ?? {}) as { item_id?: string; kind?: string; direction?: 'out' | 'in' }
  const name = l.item_id ? names.item?.(l.item_id) : undefined
  const label = linkLabel(l.kind ?? '', l.direction ?? 'out').toLowerCase()
  return `${name ? `“${name}”` : 'another item'} (${label})`
}

/** What one event did, after the actor: "added tags #a #b". */
export function eventWhat(ev: Pick<ItemEvent, 'kind' | 'before' | 'after'>, names: Names = {}): string {
  const before = (ev.before ?? {}) as Record<string, unknown>
  const after = (ev.after ?? {}) as Record<string, unknown>
  switch (ev.kind) {
    case 'create':
      return 'added it'
    case 'edit':
      return editText(before, after)
    case 'tags': {
      const b = strings(before.tags)
      const a = strings(after.tags)
      const added = lowerDiff(a, b)
      const removed = lowerDiff(b, a)
      const parts = []
      if (added.length) parts.push(`added ${added.length === 1 ? 'tag' : 'tags'} ${tagList(added)}`)
      if (removed.length) parts.push(`removed ${removed.length === 1 ? 'tag' : 'tags'} ${tagList(removed)}`)
      return parts.join(' and ') || 'changed the tags'
    }
    case 'folders': {
      const b = strings(before.folder_ids)
      const a = strings(after.folder_ids)
      const added = a.filter((f) => !b.includes(f))
      const removed = b.filter((f) => !a.includes(f))
      const parts = []
      if (added.length) parts.push(`added it to ${folderNames(added, names)}`)
      if (removed.length) parts.push(`removed it from ${folderNames(removed, names)}`)
      return parts.join(' and ') || 'changed its folders'
    }
    case 'trash':
      return 'moved it to the Trash'
    case 'restore':
      return 'restored it from the Trash'
    case 'purge':
      return 'deleted it for good'
    case 'protect':
      return after.protected ? 'protected it' : 'removed its protection'
    case 'link':
      return `linked it to ${linkText(after.link, names)}`
    case 'unlink':
      return `removed its link to ${linkText(before.link, names)}`
    default:
      return `made a change (${ev.kind})`
  }
}

/** The whole sentence: "Claude added tags #a #b". */
export function describeEvent(ev: ItemEvent, names: Names = {}): string {
  return `${actorName(ev.actor)} ${eventWhat(ev, names)}`
}

/** Whether reverting the event can work at all (a purge can't be undone). */
export function canRevert(ev: Pick<ItemEvent, 'kind'>): boolean {
  return ev.kind !== 'purge'
}

/** "just now", "2 min ago", "3 h ago", "yesterday", "4 days ago", then a date. */
export function timeAgo(at: string, now = Date.now()): string {
  const t = Date.parse(at)
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 45) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} h ago`
  const d = Math.round(h / 24)
  if (d === 1) return 'yesterday'
  if (d < 14) return `${d} days ago`
  return new Date(t).toLocaleDateString()
}

/** Past-tense verb for a batch of one kind, and whether it reads "N items". */
const BATCH_VERB: Record<string, string> = {
  create: 'added',
  edit: 'edited',
  tags: 'tagged',
  folders: 'filed',
  trash: 'trashed',
  restore: 'restored',
  purge: 'permanently deleted',
  protect: 'changed protection on',
  link: 'linked',
  unlink: 'unlinked',
}

const KIND_NOUN: Record<string, string> = {
  create: 'adds',
  edit: 'edits',
  tags: 'tags',
  folders: 'folders',
  trash: 'trash',
  restore: 'restores',
  purge: 'deletions',
  protect: 'protection',
  link: 'links',
  unlink: 'links',
}

/** "Claude via MCP tagged 12 items", "You in the web app made 5 changes
 * (tags, folders)". `count` is the number of events; a batch of one kind
 * has one event per item. */
export function batchSummary(b: Pick<Batch, 'actor' | 'source' | 'count' | 'kinds'>): string {
  const who = whoText(b.actor, b.source)
  const kinds = b.kinds ?? []
  if (kinds.length === 1 && BATCH_VERB[kinds[0]]) {
    return `${who} ${BATCH_VERB[kinds[0]]} ${b.count} item${b.count === 1 ? '' : 's'}`
  }
  const what = [...new Set(kinds.map((k) => KIND_NOUN[k] ?? k))].join(', ')
  return `${who} made ${b.count} change${b.count === 1 ? '' : 's'}${what ? ` (${what})` : ''}`
}

/** A batch made only of permanent deletions can't be undone. */
export function batchUndoable(b: Pick<Batch, 'kinds'>): boolean {
  return (b.kinds ?? []).some((k) => k !== 'purge')
}

/** A skip reason from the server in plain words: "changed later: tag:a,
 * folder:01H…" -> "it was changed again later (#a, folder “Shoes”)". */
export function reasonText(reason: string, names: Names = {}): string {
  const m = /^changed later: (.*)$/.exec(reason)
  if (!m) return reason
  const parts = m[1].split(', ').map((k) => {
    if (k.startsWith('tag:')) return `#${k.slice(4)}`
    if (k.startsWith('folder:')) {
      const n = names.folder?.(k.slice(7))
      return n ? `folder “${n}”` : 'a folder'
    }
    if (k.startsWith('link:')) return 'a link'
    if (k === 'state') return 'Trash state'
    if (k === 'protected') return 'protection'
    if (k === 'source_url') return 'source'
    if (k === 'created_at') return 'date added'
    return k
  })
  return `it was changed again later (${parts.join(', ')})`
}

/** The notice after an undo or revert. `tone` is error when anything was
 * skipped, so the reason stays on screen until dismissed. */
export function undoReport(r: UndoResult, verb = 'Undid', names: Names = {}): { message: string; tone: 'info' | 'error' } {
  const done = `${verb} ${r.undone} change${r.undone === 1 ? '' : 's'}`
  if (!r.skipped.length) return { message: `${done}.`, tone: 'info' }
  const first = reasonText(r.skipped[0].reason, names)
  const more = r.skipped.length > 1 ? ` (and ${r.skipped.length - 1} more)` : ''
  const skipped = `${r.skipped.length} skipped: ${first}${more}`
  return {
    message: r.undone ? `${done}. ${skipped}.` : `Nothing to undo: ${skipped}.`,
    tone: 'error',
  }
}
