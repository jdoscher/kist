// F3 guest proofing on share links: the owner's API calls and the pure
// helpers the Feedback view, the info panel and tiles share. Tested in
// tests/proofing.test.ts.

import { json, request } from './api.ts'

export interface ProofRound {
  n: number
  started_at: string | null
  closed_at: string | null
}

export interface ProofingSettings {
  share_id: string
  enabled: boolean
  show_all: boolean
  round: number
  round_open: boolean
  rounds: ProofRound[]
  seen_at: string | null
  marks: number
  comments: number
  unseen: number
}

export interface ProofingShareSummary extends ProofingSettings {
  title: string
  status: 'active' | 'expired' | 'revoked'
  slug: string
}

export interface FeedbackSummary {
  unseen: number
  shares: ProofingShareSummary[]
}

interface RowBase {
  id: string
  share_id: string
  share_title: string
  round: number
  guest_id: string
  guest_name: string
  item_id: string
  item_filename: string | null
  item_state: 'active' | 'trashed' | 'purged'
  item_version: number
  seen_version_changed: boolean
  has_newer_version: boolean
  at: string
  unseen: boolean
  accepted: boolean
}

export interface MarkRow extends RowBase {
  kind: 'mark'
  verdict: 'pick' | 'reject' | null
  heart: boolean
  accepted_as: string | null
}

export interface CommentRow extends RowBase {
  kind: 'comment'
  text: string
  annotation_id: string | null
}

export type FeedbackRow = MarkRow | CommentRow

export interface GuestCounts {
  picks: number
  rejects: number
  hearts: number
  comments: number
}

export interface AcceptResult {
  batch_id: string | null
  accepted: string[]
  skipped: { id: string; reason: string }[]
}

export type AcceptAs = 'pick' | 'reject' | `label:${string}`

export const proofApi = {
  settings: (shareId: string) => request<ProofingSettings>(`/api/shares/${encodeURIComponent(shareId)}/proofing`),
  patch: (shareId: string, body: Partial<Pick<ProofingSettings, 'enabled' | 'show_all' | 'round_open'>>) =>
    request<ProofingSettings>(`/api/shares/${encodeURIComponent(shareId)}/proofing`, { ...json(body), method: 'PATCH' }),
  newRound: (shareId: string) => request<ProofingSettings>(`/api/shares/${encodeURIComponent(shareId)}/proofing/rounds`, { method: 'POST' }),
  list: (params: { share_id?: string; round?: number; item_id?: string; unseen?: boolean; limit?: number } = {}) =>
    request<FeedbackRow[]>(`/api/feedback?${feedbackParams(params)}`),
  summary: () => request<FeedbackSummary>('/api/feedback/summary'),
  counts: () => request<Record<string, GuestCounts>>('/api/feedback/counts'),
  seen: (shareId?: string) => request<{ marked: number }>('/api/feedback/seen', json({ share_id: shareId ?? null })),
  accept: (body: { mark_ids?: string[]; comment_ids?: string[]; as?: AcceptAs }) => request<AcceptResult>('/api/feedback/accept', json(body)),
  deleteComment: (id: string) => request<{ status: string }>(`/api/feedback/comments/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

export function feedbackParams(p: { share_id?: string; round?: number; item_id?: string; unseen?: boolean; limit?: number }): string {
  const s = new URLSearchParams()
  if (p.share_id) s.set('share_id', p.share_id)
  if (p.round) s.set('round', String(p.round))
  if (p.item_id) s.set('item_id', p.item_id)
  if (p.unseen) s.set('unseen', 'true')
  s.set('limit', String(p.limit ?? 500))
  return s.toString()
}

/** One item's feedback in one share and round, as the Feedback view lists it. */
export interface ItemGroup {
  key: string
  item_id: string
  item_filename: string | null
  item_state: RowBase['item_state']
  share_id: string
  share_title: string
  round: number
  picks: MarkRow[]
  rejects: MarkRow[]
  hearts: MarkRow[]
  comments: CommentRow[]
  /** marks not accepted yet (what "Accept picks" sends) */
  openPicks: MarkRow[]
  newest: string
  unseen: boolean
  has_newer_version: boolean
  versions: number[]
}

/** Rows grouped per item (and share and round), most recent activity first;
 * items with more picks first among equals is left to the reader: the
 * owner reads newest first, like an inbox. */
export function groupByItem(rows: FeedbackRow[]): ItemGroup[] {
  const groups = new Map<string, ItemGroup>()
  for (const r of rows) {
    const key = `${r.share_id}:${r.round}:${r.item_id}`
    let g = groups.get(key)
    if (!g) {
      g = {
        key,
        item_id: r.item_id,
        item_filename: r.item_filename,
        item_state: r.item_state,
        share_id: r.share_id,
        share_title: r.share_title,
        round: r.round,
        picks: [],
        rejects: [],
        hearts: [],
        comments: [],
        openPicks: [],
        newest: r.at,
        unseen: false,
        has_newer_version: false,
        versions: [],
      }
      groups.set(key, g)
    }
    if (r.at > g.newest) g.newest = r.at
    g.unseen ||= r.unseen
    g.has_newer_version ||= r.has_newer_version
    if (!g.versions.includes(r.item_version)) g.versions.push(r.item_version)
    if (r.kind === 'comment') g.comments.push(r)
    else {
      if (r.verdict === 'pick') g.picks.push(r)
      if (r.verdict === 'reject') g.rejects.push(r)
      if (r.heart) g.hearts.push(r)
      if ((r.verdict === 'pick' || (r.heart && r.verdict !== 'reject')) && !r.accepted) g.openPicks.push(r)
    }
  }
  for (const g of groups.values()) {
    g.comments.sort((a, b) => a.at.localeCompare(b.at))
    g.versions.sort((a, b) => a - b)
  }
  return [...groups.values()].sort((a, b) => b.newest.localeCompare(a.newest) || a.key.localeCompare(b.key))
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** "2 picks · 1 ♥ · 1 reject · 3 comments" (empty parts left out). */
export function countsText(c: Partial<GuestCounts>): string {
  const parts: string[] = []
  if (c.picks) parts.push(plural(c.picks, 'pick'))
  if (c.hearts) parts.push(`${c.hearts} ♥`)
  if (c.rejects) parts.push(plural(c.rejects, 'reject'))
  if (c.comments) parts.push(plural(c.comments, 'comment'))
  return parts.join(' · ')
}

/** Short tile badge: hearts and picks only (what proofing is for). */
export function tileBadgeText(c: GuestCounts | undefined): string {
  if (!c) return ''
  const parts: string[] = []
  if (c.picks) parts.push(`✓${c.picks}`)
  if (c.hearts) parts.push(`♥${c.hearts}`)
  if (!parts.length && c.comments) parts.push(`💬${c.comments}`)
  return parts.join(' ')
}

export function tileBadgeLabel(c: GuestCounts | undefined): string {
  const t = c ? countsText(c) : ''
  return t ? `Guest feedback: ${t}` : ''
}

/** "Ann, Bo and 2 others" */
export function namesText(rows: { guest_name: string }[], max = 2): string {
  const names = [...new Set(rows.map((r) => r.guest_name))]
  if (names.length <= max) return names.length === 2 ? `${names[0]} and ${names[1]}` : names.join('')
  return `${names.slice(0, max).join(', ')} and ${plural(names.length - max, 'other')}`
}

export function roundText(s: Pick<ProofingSettings, 'round' | 'round_open' | 'enabled'>): string {
  if (!s.enabled) return 'Guest feedback off'
  return `Round ${s.round} · ${s.round_open ? 'open' : 'closed'}`
}

export function acceptedText(r: AcceptResult): string {
  const n = r.accepted.length
  const skipped = r.skipped.length ? ` ${plural(r.skipped.length, 'skipped', 'skipped')} (${r.skipped[0].reason}).` : ''
  return n ? `Accepted ${plural(n, 'piece')} of guest feedback.${skipped}` : `Nothing accepted.${skipped}`
}

/** What a row says in the timeline of an item's feedback. */
export function markText(r: MarkRow): string {
  const parts: string[] = []
  if (r.verdict === 'pick') parts.push('picked')
  if (r.verdict === 'reject') parts.push('rejected')
  if (r.heart) parts.push('hearted')
  return parts.join(' and ') || 'cleared'
}

export function versionNote(g: Pick<ItemGroup, 'has_newer_version' | 'versions'>): string {
  const seen = g.versions.length > 1 ? `versions ${g.versions.join(', ')}` : `version ${g.versions[0] ?? 1}`
  return g.has_newer_version ? `Guests saw ${seen}; there's a newer version now` : g.versions.some((v) => v > 1) ? `Guests saw ${seen}` : ''
}
