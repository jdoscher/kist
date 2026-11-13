// F4 API calls (kist/api/credits.py). Apart from api.ts so the first page
// doesn't carry them.

import { json, request, withBatch, type Item } from './api'
import type { Clearance, PrintInfo } from './credits'

export interface CreditSummary {
  needs_credit: number
  uncleared: number
  ai_generated: number
}

export interface ItemCredits {
  line: string | null
  source: string | null
  license: string | null
  rights_status: string | null
  text: string | null
  caption: { title: string | null; credit: string | null; rights_status: string | null }
  /** what auto-fill would add from the item's own files (nothing changed yet) */
  suggestions: Record<string, unknown>
}

export interface ClearanceTarget {
  kind: 'folder' | 'smart' | 'board' | 'items' | 'all'
  id: string | null
  itemIds?: string[]
}

export const creditsApi = {
  settings: () => request<{ clearance_warning: boolean }>('/api/credits/settings'),
  setSettings: (patch: { clearance_warning: boolean }) =>
    request<{ clearance_warning: boolean }>('/api/credits/settings', { ...json(patch), method: 'PATCH' }),
  summary: () => request<CreditSummary>('/api/credits/summary'),
  clearance: (t: ClearanceTarget) =>
    request<Clearance>('/api/credits/clearance', json({ target: t.kind, target_id: t.id, item_ids: t.itemIds ?? null })),
  bulk: (itemIds: string[], provenance: Record<string, unknown>, batch: string) =>
    request<{ updated: number; failed: { id: string; error: string }[]; batch_id: string }>(
      '/api/credits/bulk',
      withBatch(json({ item_ids: itemIds, provenance }), batch),
    ),
  print: (id: string) => request<PrintInfo>(`/api/items/${id}/print`),
  item: (id: string) => request<ItemCredits>(`/api/items/${id}/credits`),
  autofill: (id: string) =>
    request<{ filled: Record<string, unknown>; item: Item }>(`/api/items/${id}/credits/autofill`, json({})),
}
