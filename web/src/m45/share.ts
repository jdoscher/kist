// Share links (Milestone 4): the dialog's form as the POST /api/shares
// body, and how a share's state reads. Pure, tested in tests/m45.test.ts.

export type ShareTargetKind = 'folder' | 'smart' | 'board' | 'items'
export type ExpiryChoice = '1' | '7' | '30' | '90' | '365' | 'never' | 'date'

export const EXPIRY_CHOICES: [ExpiryChoice, string][] = [
  ['1', '1 day'],
  ['7', '1 week'],
  ['30', '30 days'],
  ['90', '90 days'],
  ['365', '1 year'],
  ['never', 'Never'],
  ['date', 'On a date…'],
]

export interface ShareForm {
  kind: ShareTargetKind
  targetId: string | null
  itemIds: string[]
  title: string
  usePassword: boolean
  password: string
  expiry: ExpiryChoice
  /** YYYY-MM-DD, for expiry 'date' */
  date: string
  allowDownload: boolean
  allowOriginal: boolean
}

export function defaultShareForm(kind: ShareTargetKind, targetId: string | null, itemIds: string[] = [], title = ''): ShareForm {
  return {
    kind,
    targetId,
    itemIds,
    title,
    usePassword: false,
    password: '',
    // the server's default too
    expiry: '30',
    date: '',
    allowDownload: false,
    allowOriginal: false,
  }
}

export interface SharePayload {
  target_kind: ShareTargetKind
  target_id?: string
  item_ids?: string[]
  title: string
  password?: string
  expires_in_days?: number
  expires_at?: string
  allow_download: boolean
  allow_original: boolean
}

export const SHARE_TITLE_MAX = 200
export const SHARE_PASSWORD_MIN = 4
export const SHARE_ITEMS_MAX = 1000

/** What's wrong with the form, in words; empty when it can be sent. */
export function shareFormErrors(f: ShareForm, now: Date = new Date()): string[] {
  const out: string[] = []
  if (f.kind === 'items' ? f.itemIds.length === 0 : !f.targetId) out.push('Nothing to share.')
  if (f.kind === 'items' && f.itemIds.length > SHARE_ITEMS_MAX) out.push(`A link can hold at most ${SHARE_ITEMS_MAX} items.`)
  if (f.title.trim().length > SHARE_TITLE_MAX) out.push(`The title is at most ${SHARE_TITLE_MAX} characters.`)
  if (f.usePassword && f.password.length < SHARE_PASSWORD_MIN)
    out.push(`The password needs at least ${SHARE_PASSWORD_MIN} characters.`)
  if (f.expiry === 'date') {
    const d = endOfDay(f.date)
    if (!d) out.push('Pick the day the link stops working.')
    else if (d.getTime() <= now.getTime()) out.push('That day has passed: pick a later one.')
  }
  return out
}

function endOfDay(ymd: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(ymd)) return null
  const d = new Date(`${ymd}T23:59:59`)
  return Number.isNaN(d.getTime()) ? null : d
}

/** The POST /api/shares body. The original needs downloads on (the server
 * refuses allow_original without allow_download). */
export function sharePayload(f: ShareForm): SharePayload {
  const p: SharePayload = {
    target_kind: f.kind,
    title: f.title.trim(),
    allow_download: f.allowDownload,
    allow_original: f.allowDownload && f.allowOriginal,
  }
  if (f.kind === 'items') p.item_ids = [...new Set(f.itemIds)]
  else if (f.targetId) p.target_id = f.targetId
  if (f.usePassword && f.password) p.password = f.password
  if (f.expiry === 'never') p.expires_in_days = 0
  else if (f.expiry === 'date') {
    const d = endOfDay(f.date)
    if (d) p.expires_at = d.toISOString()
  } else p.expires_in_days = Number(f.expiry)
  return p
}

export interface Share {
  id: string
  slug: string
  url: string
  target_kind: ShareTargetKind
  target_id: string | null
  item_ids: string[] | null
  title: string
  has_password: boolean
  expires_at: string | null
  allow_download: boolean
  allow_original: boolean
  created_by: string
  created_at: string
  revoked_at: string | null
  view_count: number
  last_viewed_at: string | null
  status: 'active' | 'expired' | 'revoked'
}

/** The link to copy: the server's url when it knows its public address,
 * else this page's origin + /s/<slug>. */
export function shareLink(s: Pick<Share, 'url' | 'slug'>, origin: string): string {
  if (/^https?:\/\//.test(s.url)) return s.url
  return `${origin.replace(/\/+$/, '')}/s/${s.slug}`
}

export function shareStatusText(s: Pick<Share, 'status' | 'expires_at' | 'revoked_at'>, now: Date = new Date()): string {
  if (s.status === 'revoked') return 'Revoked'
  if (s.status === 'expired') return 'Expired'
  if (!s.expires_at) return 'Never expires'
  const days = Math.ceil((new Date(s.expires_at).getTime() - now.getTime()) / 86_400_000)
  if (days <= 1) return 'Expires within a day'
  return `Expires in ${days} days`
}

export function shareWhat(kind: ShareTargetKind, count?: number): string {
  if (kind === 'folder') return 'folder'
  if (kind === 'smart') return 'smart folder'
  if (kind === 'board') return 'board'
  return count === 1 ? '1 item' : `${count ?? 'these'} items`
}
