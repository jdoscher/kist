// Milestone 2 endpoints the new screens use: palettes, captions, tag
// suggestions, the vocabulary and tag cleanup, duplicates, stacks,
// suggested collections and people. The request plumbing (token, errors,
// batch header) is api.ts's.

import { json, request, withBatch, type Item } from './api.ts'

export interface PaletteColour {
  hex: string
  pct: number
  name: string
}

export interface TagSuggestion {
  tag: string
  score: number
  why: 'similar items' | 'matches the image' | string
}

export interface Vocabulary {
  aliases: { alias: string; tag: string }[]
  implies: { tag: string; implied: string }[]
  namespaces: { namespace: string; tags: number }[]
}

export type CleanupKind = 'one_off' | 'clash' | 'near' | 'unused' | 'smart_folder'

export interface CleanupSuggestion {
  kind: CleanupKind
  tags: string[]
  counts: Record<string, number>
  proposal: {
    action: 'merge' | 'review' | 'delete' | 'edit_smart_folder'
    into?: string
    from?: string[]
    smart_folder_id?: string
    smart_folder?: string
    missing_tags?: string[]
    missing_folder?: string | null
  }
  last_used?: string | null
}

export interface MergeResult {
  into: string
  from: string[]
  items: number
  counts: Record<string, number>
  smart_folders: unknown[]
  dry_run: boolean
  batch_id: string
  renamed?: boolean
}

export type DuplicateKind = 'same_image' | 'similar' | 'exact'

export interface DuplicateGroup {
  id: string
  kind: DuplicateKind
  items: Item[]
  pairs: { a: string; b: string; score: number }[]
  suggested_keep: string
}

export interface MergeOptions {
  tags: boolean
  folders: boolean
  notes: boolean
  rating: 'max' | 'keep'
  source_url: boolean
}

export interface StackSuggestion {
  kind: 'raw_jpeg' | 'burst' | 'version' | string
  item_ids: string[]
  cover_item_id: string
  reason: string
}

export interface StackOut {
  id: string
  cover_item_id: string
  count: number
  items: Item[]
}

export interface Cluster {
  id: string
  size: number
  item_ids: string[]
  sample_item_ids: string[]
  label_suggestion: string | null
  label_source?: 'tags' | 'zero_shot' | null
  common_tags?: { tag: string; count: number }[]
  zero_shot_tags?: { tag: string; score: number }[]
  cohesion?: number
  top_folder?: { id: string; name: string | null; count: number } | null
}

export interface Collections {
  computed_at?: string
  clusters: Cluster[]
}

export interface Face {
  id: number
  item_id: string
  idx?: number
  box: { x: number; y: number; w: number; h: number }
  score?: number
  person_id: string | null
  person_name?: string | null
  thumb_url: string
}

export interface Person {
  id: string
  name: string | null
  face_count: number
  item_count: number
  cover: Face | null
}

const enc = encodeURIComponent

export const org = {
  palette: (id: string) => request<PaletteColour[]>(`/api/items/${id}/palette`),
  caption: (id: string) =>
    request<{ job_id: number; status: string; provider: string | null; warning: string | null }>(
      `/api/items/${id}/caption`,
      { method: 'POST' },
    ),
  tagSuggestions: (id: string, limit = 8) => request<TagSuggestion[]>(`/api/items/${id}/tag-suggestions?limit=${limit}`),
  tagFeedback: (id: string, tag: string, accepted: boolean) =>
    request<unknown>(`/api/items/${id}/tag-suggestions/feedback`, json({ tag, accepted })),
  faces: (id: string) => request<Face[]>(`/api/items/${id}/faces`),

  vocabulary: () => request<Vocabulary>('/api/vocabulary'),
  addAlias: (alias: string, tag: string, batch?: string) =>
    request<{ alias: string; tag: string; items: number }>('/api/vocabulary/aliases', withBatch(json({ alias, tag }), batch)),
  removeAlias: (alias: string) => request<{ removed: boolean }>(`/api/vocabulary/aliases?alias=${enc(alias)}`, { method: 'DELETE' }),
  addImplies: (tag: string, implied: string) => request<unknown>('/api/vocabulary/implies', json({ tag, implied })),
  removeImplies: (tag: string, implied: string) =>
    request<{ removed: boolean }>(`/api/vocabulary/implies?tag=${enc(tag)}&implied=${enc(implied)}`, { method: 'DELETE' }),

  cleanup: () => request<CleanupSuggestion[]>('/api/tags/cleanup'),
  mergeTags: (from: string[], into: string, opts: { dryRun?: boolean; keepAlias?: boolean; batch?: string } = {}) =>
    request<MergeResult>(
      `/api/tags/merge${opts.dryRun ? '?dry_run=1' : ''}`,
      withBatch(json({ from, into, keep_alias: !!opts.keepAlias }), opts.batch),
    ),
  renameTag: (from: string, to: string, opts: { dryRun?: boolean; batch?: string } = {}) =>
    request<MergeResult>(`/api/tags/rename${opts.dryRun ? '?dry_run=1' : ''}`, withBatch(json({ from, to }), opts.batch)),
  deleteUnusedTags: () => request<{ deleted: number }>('/api/tags/unused', { method: 'DELETE' }),

  notDuplicate: (a: string, b: string, batch?: string) =>
    request<unknown>(`/api/items/${a}/links`, withBatch(json({ dst_id: b, kind: 'not_duplicate' }), batch)),
  duplicateGroups: (kind: DuplicateKind) => request<DuplicateGroup[]>(`/api/duplicates/groups?kind=${kind}`, {}, 60_000),
  resolveDuplicates: (keep: string, others: string[], merge: MergeOptions, batch?: string) =>
    request<{ keep: Item; trashed: string[]; batch_id?: string }>(
      '/api/duplicates/resolve',
      withBatch(json({ keep, others, merge }), batch),
    ),

  stackSuggestions: () => request<StackSuggestion[]>('/api/stacks/suggestions'),
  stack: (id: string) => request<StackOut>(`/api/stacks/${id}`),
  createStack: (item_ids: string[], cover?: string, batch?: string) =>
    request<StackOut>('/api/stacks', withBatch(json({ item_ids, cover }), batch)),
  patchStack: (id: string, patch: { cover?: string; add?: string[]; remove?: string[] }, batch?: string) =>
    request<StackOut | { id: string; dissolved: true }>(`/api/stacks/${id}`, withBatch({ ...json(patch), method: 'PATCH' }, batch)),
  unstack: (id: string, batch?: string) =>
    request<{ unstacked: number }>(`/api/stacks/${id}`, withBatch({ method: 'DELETE' }, batch)),

  collections: (refresh = false) => request<Collections>(`/api/collections/suggested${refresh ? '?refresh=1' : ''}`, {}, 120_000),
  acceptCollection: (id: string, name: string, kind: 'folder' | 'smart_folder', batch?: string) =>
    request<{ id: string; items: number; folder_id?: string; smart_folder_id?: string; tag?: string; batch_id: string }>(
      `/api/collections/suggested/${enc(id)}/accept`,
      withBatch(json({ name, kind }), batch),
    ),

  people: () => request<Person[]>('/api/people'),
  patchPerson: (id: string, patch: { name?: string | null; cover_face_id?: number }) =>
    request<Person>(`/api/people/${id}`, { ...json(patch), method: 'PATCH' }),
  mergePeople: (from: string[], into: string) => request<Person>('/api/people/merge', json({ from, into })),
  patchFace: (id: number, person_id: string | null) =>
    request<Face>(`/api/faces/${id}`, { ...json({ person_id }), method: 'PATCH' }),
}
