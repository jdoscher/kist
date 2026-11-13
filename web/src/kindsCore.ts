// F1 utility collections ("Kinds"): types, names and the pure helpers the
// sidebar, kind bar, info panel, filter chips and Settings share. No React
// and no imports (m45/filters.ts uses it too); REST calls are in kinds.ts.
// Tested in tests/kinds.test.ts.
//
// A kind is a filter like any other: GET /api/items?auto_kind=<id> (and
// -<id>, <id>:unsure, <id>:auto, <id>:confirmed, <id>:rejected). It rides in
// the Query as `auto_kind` (m45/filters.ts), so the URL, smart folders and
// filter chips carry it.

export interface KindInfo {
  id: string
  name: string
  packs: string[]
  parent: string | null
  enabled: boolean
  enabled_override: boolean | null
  opt_in: boolean
  rule: string | null
  clip: boolean
  learnable: boolean
  strictness: number
  threshold: number
  count: number
  confirmed: number
  rejected: number
  visible: boolean
  learned: { corrections: number; pos: number; neg: number; trained_at: string } | null
  learn_min: number
  how: string
}

export interface KindsOverview {
  enabled: boolean
  kinds: KindInfo[]
  packs: { id: string; name: string; enabled: boolean }[]
  min_items: number
  low_res_px: number
  /** with F4's print check: Low-res = prints under this many inches at 300 dpi */
  low_res_in?: number
  print_size_source: string
  pending_jobs: number
}

export interface ItemKind {
  kind: string
  name: string
  parent: string | null
  source: 'auto' | 'human'
  member: boolean
  verdict: boolean | null
  score: number | null
  why: string | null
  enabled: boolean
}

export interface CorrectResult {
  kind: string
  verdict: boolean | null
  changed: number
  item_ids: string[]
  batch_id: string
}

/** The names the server uses (kist/services/kinds_catalog.py; a backend
 * test checks they match), so a title or chip has a name before the
 * overview has loaded. */
export const KIND_NAMES: Record<string, string> = {
  screenshots: 'Screenshots',
  documents: 'Documents & pages',
  drawings: 'Drawings',
  pencil: 'Pencil & graphite',
  ink: 'Ink & line art',
  paintings: 'Paintings',
  photos: 'Photos (from a camera)',
  'low-res': 'Low-res',
  'ai-made': 'AI-made',
  runway: 'Runway',
  flats: 'Technical flats',
  'product-on-white': 'Product on white',
  fabric: 'Fabric close-ups',
  prints: 'Prints & repeats',
  'colour-cards': 'Colour cards',
  plans: 'Plans, sections & elevations',
  renders: 'Renders & 3D',
  'built-work': 'Built work & site',
  materials: 'Material close-ups',
  models: 'Physical models',
  whiteboards: 'Whiteboards & markups',
  'film-stills': 'Film & TV stills',
  storyboards: 'Storyboards',
  textures: 'Textures',
  hdris: 'HDRIs & panoramas',
  'colour-scripts': 'Colour scripts',
  lettering: 'Lettering & type',
  logos: 'Logos & marks',
  'printed-matter': 'Printed matter',
  'relief-prints': 'Relief & intaglio prints',
}

export type KindMode = 'auto' | 'confirmed' | 'unsure' | 'rejected'

export interface KindFilter {
  id: string
  not: boolean
  mode: KindMode | null
}

const FILTER = /^(-)?([a-z0-9-]{1,40})(?::(auto|confirmed|unsure|rejected))?$/

/** One auto_kind value (only the first of a comma list), or null. */
export function parseKindFilter(v: string | null | undefined): KindFilter | null {
  if (!v) return null
  const m = FILTER.exec(v.split(',')[0].trim().toLowerCase())
  if (!m) return null
  return { id: m[2], not: !!m[1], mode: (m[3] as KindMode | undefined) ?? null }
}

export function kindFilterValue(f: KindFilter): string {
  return `${f.not ? '-' : ''}${f.id}${f.mode ? `:${f.mode}` : ''}`
}

/** A valid auto_kind value as the server takes it, or null (URLs). */
export function cleanKindFilter(v: string): string | null {
  const parts = v
    .split(',')
    .map((p) => p.trim().toLowerCase())
    .filter(Boolean)
  if (!parts.length || parts.some((p) => !FILTER.test(p))) return null
  return parts.join(',')
}

export function kindName(id: string, overview?: KindsOverview | null): string {
  return overview?.kinds.find((k) => k.id === id)?.name ?? KIND_NAMES[id] ?? id
}

const MODE_TEXT: Record<KindMode, string> = {
  auto: 'not yet confirmed',
  confirmed: 'confirmed',
  unsure: 'to review',
  rejected: 'ruled out',
}

/** The grid title / chip text for an auto_kind value. */
export function kindTitle(v: string, overview?: KindsOverview | null): string {
  const f = parseKindFilter(v)
  if (!f) return v
  const name = kindName(f.id, overview)
  if (f.not) return `Not ${name}`
  return f.mode ? `${name}, ${MODE_TEXT[f.mode]}` : name
}

/** The sidebar's kinds: enabled ones with enough items (min_items), plus
 * the one on screen; children right under their parent, indented. */
export function sidebarKinds(o: KindsOverview | undefined, current: string | null = null): (KindInfo & { depth: number })[] {
  if (!o || !o.enabled) return []
  const show = (k: KindInfo) => k.enabled && (k.visible || k.id === current)
  const out: (KindInfo & { depth: number })[] = []
  for (const k of o.kinds) {
    if (k.parent) continue
    const kids = o.kinds.filter((c) => c.parent === k.id && show(c))
    if (!show(k) && !kids.length) continue
    out.push({ ...k, depth: 0 })
    for (const c of kids) out.push({ ...c, depth: 1 })
  }
  return out
}

/** Settings groups: each pack and its kinds (a kind in two packs shows in
 * the first one only). */
export function kindsByPack(o: KindsOverview): { pack: KindsOverview['packs'][number]; kinds: KindInfo[] }[] {
  const seen = new Set<string>()
  return o.packs.map((pack) => {
    const kinds = o.kinds.filter((k) => k.packs.includes(pack.id) && !seen.has(k.id))
    for (const k of kinds) seen.add(k.id)
    return { pack, kinds }
  })
}

/** Strictness as the slider's words. */
export function strictnessText(s: number): string {
  if (s < 0.2) return 'Very loose'
  if (s < 0.4) return 'Loose'
  if (s <= 0.6) return 'Balanced'
  if (s <= 0.8) return 'Strict'
  return 'Very strict'
}

/** How a kind decides, in a line for its bar. */
export function learningText(k: KindInfo): string {
  if (!k.learnable) return 'Rule-based: corrections are kept, nothing to learn'
  if (k.learned) return `Learned from ${k.learned.corrections} corrections`
  const n = k.confirmed + k.rejected
  const left = Math.max(0, k.learn_min - n)
  return left > 0 ? `Learns after ${left} more correction${left === 1 ? '' : 's'}` : 'Learning from your corrections…'
}

/** Whether an item leaves the kind view in `mode` after a verdict (X:
 * false, B: true), so the full-screen preview moves on first. */
export function leavesList(mode: KindMode | null, verdict: boolean): boolean {
  if (mode === 'unsure') return true // answered: not in the review queue any more
  if (mode === 'rejected') return verdict
  return !verdict
}
