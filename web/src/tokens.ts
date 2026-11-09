// Token scopes in plain words, and what the signed-in token may do in the
// web app (no React; tests/tokens.test.ts). The scopes themselves are the
// server's (kist/access.py); the presets are the few combinations people
// actually need, each with an explanation.

import type { Me } from './api.ts'

export type PresetId = 'family' | 'share' | 'agent' | 'helper' | 'full'

export interface Preset {
  id: PresetId
  label: string
  scopes: string[]
  /** a read-only token can be limited to some folders */
  folders: boolean
  explain: string
}

export const PRESETS: Preset[] = [
  {
    id: 'family',
    label: 'Family read-only',
    scopes: ['read'],
    folders: true,
    explain:
      'Can look but never change anything. Tick folders to show only those (and their subfolders); leave them all unticked to show the whole library.',
  },
  {
    id: 'share',
    label: 'Share sheet (add only)',
    scopes: ['add'],
    folders: false,
    explain:
      'Can only add new items, e.g. from the iPhone share sheet, a Shortcut or the browser extension. It can’t see what’s in the library.',
  },
  {
    id: 'agent',
    label: 'Agent',
    scopes: ['agent'],
    folders: false,
    explain:
      'For Claude and other AI agents over MCP: can search, tag, file, link, protect and trash, but never delete for good or remove protection. Everything it does can be undone in Recent changes.',
  },
  {
    id: 'helper',
    label: 'Helper (Mac AI helper)',
    scopes: ['helper'],
    folders: false,
    explain:
      'For a Mac that runs AI jobs (image understanding, reading text) for this server with “kist helper”. It can’t browse or edit anything.',
  },
  {
    id: 'full',
    label: 'Full access',
    scopes: ['admin'],
    folders: false,
    explain:
      'Everything, including deleting for good and managing tokens. Treat it like the owner’s token: only for your own devices.',
  },
]

const SCOPE_LABEL: Record<string, string> = {
  admin: 'Full access',
  write: 'Edit everything',
  agent: 'Agent',
  add: 'Add only',
  read: 'Read-only',
  helper: 'AI helper',
}

/** The body for POST /api/tokens from the dialog's choices. Folders only
 * travel with a read-only token. */
export function tokenRequest(
  name: string,
  presetId: PresetId,
  folderIds: string[] = [],
  expires: string | null = null,
): { name: string; scopes: string[]; folder_ids: string[]; expires_at: string | null } {
  const preset = PRESETS.find((p) => p.id === presetId) ?? PRESETS[0]
  return {
    name: name.trim(),
    scopes: [...preset.scopes],
    folder_ids: preset.folders ? [...folderIds] : [],
    // a date from <input type="date"> means "through the end of that day"
    expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : null,
  }
}

/** A token's scopes as one plain label: the preset it matches, else the
 * scope names ("Read-only, Add only"). */
export function scopeText(scopes: string[], folderIds: string[] = []): string {
  const key = [...scopes].sort().join(',')
  const preset = PRESETS.find((p) => [...p.scopes].sort().join(',') === key)
  if (preset?.id === 'family') return folderIds.length ? 'Family read-only' : 'Read-only'
  if (preset) return preset.label
  return scopes.map((s) => SCOPE_LABEL[s] ?? s).join(', ') || 'No access'
}

/** Whether a token is in use, revoked or past its date. */
export function tokenStatus(
  t: { revoked_at: string | null; expires_at: string | null },
  now = Date.now(),
): 'active' | 'revoked' | 'expired' {
  if (t.revoked_at) return 'revoked'
  if (t.expires_at && Date.parse(t.expires_at) <= now) return 'expired'
  return 'active'
}

/** What the signed-in token may do in the web app. */
export interface Caps {
  /** load-bearing for the token screen: it can open the library at all */
  canBrowse: boolean
  canEdit: boolean
  /** delete for good, remove protection */
  canDestroy: boolean
  isAdmin: boolean
  /** a read token limited to some folders: no tags, smart folders, stats */
  restricted: boolean
}

export function capsOf(me: Pick<Me, 'scopes' | 'folder_ids'> | null | undefined): Caps {
  // before /api/me answers (or on an older server without it): the owner
  if (!me) return { canBrowse: true, canEdit: true, canDestroy: true, isAdmin: true, restricted: false }
  const s = new Set(me.scopes)
  const isAdmin = s.has('admin')
  const canDestroy = isAdmin || s.has('write')
  const canEdit = canDestroy || s.has('agent')
  const canBrowse = canEdit || s.has('read')
  return { canBrowse, canEdit, canDestroy, isAdmin, restricted: !isAdmin && !canEdit && me.folder_ids.length > 0 }
}

/** Why a token can't open the library (add-only, helper), or null. */
export function cantBrowseText(me: Pick<Me, 'scopes'>): string | null {
  if (capsOf({ ...me, folder_ids: [] }).canBrowse) return null
  if (me.scopes.includes('add'))
    return 'This token can only add items (for the share sheet or a Shortcut), so it can’t open the library.'
  if (me.scopes.includes('helper')) return 'This is an AI helper token for “kist helper”; it can’t open the library.'
  return 'This token can’t open the library.'
}

/** The command that starts the Mac helper with a new token. */
export function helperCommand(origin: string, token: string): string {
  return `kist helper --url ${origin} --token ${token}`
}
