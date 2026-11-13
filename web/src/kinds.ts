// F1 utility collections ("Kinds"): the REST calls, plus everything in
// kindsCore.ts (types, names, pure helpers).

import { request } from './api.ts'
import type { CorrectResult, ItemKind, KindInfo, KindsOverview } from './kindsCore.ts'

export * from './kindsCore.ts'

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const kindsApi = {
  overview: () => request<KindsOverview>('/api/kinds'),
  patch: (body: {
    packs?: Record<string, boolean>
    min_items?: number
    low_res_px?: number
    kinds?: Record<string, { enabled?: boolean | null; strictness?: number }>
  }) => request<KindsOverview>('/api/kinds', json(body, 'PATCH')),
  patchKind: (id: string, body: { enabled?: boolean | null; strictness?: number }) =>
    request<KindInfo>(`/api/kinds/${encodeURIComponent(id)}`, json(body, 'PATCH')),
  correct: (id: string, itemIds: string[], verdict: boolean | null) =>
    request<CorrectResult>(`/api/kinds/${encodeURIComponent(id)}/correct`, json({ item_ids: itemIds, verdict })),
  refresh: () => request<{ queued: boolean }>('/api/kinds/refresh', { method: 'POST' }),
  itemKinds: (itemId: string) => request<ItemKind[]>(`/api/items/${encodeURIComponent(itemId)}/kinds`),
}

