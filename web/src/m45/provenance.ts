// Provenance (Milestone 4): where an item came from. The info panel edits
// it; a save sends only what changed (PATCH {provenance}: merged, null
// removes a field). Pure, tested in tests/m45.test.ts.

export interface ProvField {
  key: string
  label: string
  kind: 'url' | 'text' | 'date'
  hint?: string
}

/** The editable fields, in the order the panel shows them. */
export const PROV_FIELDS: ProvField[] = [
  { key: 'page_url', label: 'Page', kind: 'url', hint: 'The page it came from' },
  { key: 'image_url', label: 'Image URL', kind: 'url' },
  { key: 'page_title', label: 'Page title', kind: 'text' },
  { key: 'creator', label: 'Creator', kind: 'text' },
  { key: 'credit', label: 'Credit', kind: 'text' },
  { key: 'license', label: 'Licence', kind: 'text', hint: 'e.g. CC BY 4.0, All rights reserved' },
  { key: 'rights_url', label: 'Rights URL', kind: 'url' },
]

export const METHOD_TEXT: Record<string, string> = {
  upload: 'Uploaded',
  paste: 'Pasted',
  url: 'Added from a URL',
  extension: 'Browser extension',
  bookmarklet: 'Bookmarklet',
  inbox: 'Inbox folder',
  mcp: 'An agent (MCP)',
  import: 'Imported',
}

export const LICENSE_SUGGESTIONS = [
  'All rights reserved',
  'CC0 1.0',
  'CC BY 4.0',
  'CC BY-SA 4.0',
  'CC BY-NC 4.0',
  'CC BY-NC-ND 4.0',
  'Public domain',
]

export type Provenance = Record<string, unknown>
export type AiChoice = 'unknown' | 'yes' | 'no'

export function aiChoice(p: Provenance | undefined | null): AiChoice {
  const v = p?.ai_generated
  return v === true ? 'yes' : v === false ? 'no' : 'unknown'
}

export function aiValue(c: AiChoice): boolean | null {
  return c === 'yes' ? true : c === 'no' ? false : null
}

function norm(v: unknown): unknown {
  if (typeof v === 'string') {
    const t = v.trim()
    return t === '' ? null : t
  }
  return v === undefined ? null : v
}

/** The PATCH body for what changed between the saved provenance and the
 * form: changed fields, null for cleared ones; {} when nothing changed. */
export function provenanceDiff(before: Provenance | null | undefined, after: Provenance): Provenance {
  const out: Provenance = {}
  const b = before ?? {}
  for (const [k, raw] of Object.entries(after)) {
    const now = norm(raw)
    const was = norm(b[k])
    if (now !== was) out[k] = now
  }
  return out
}

/** URLs in the form: empty is fine, otherwise an absolute http(s) URL. */
export function provenanceErrors(form: Provenance): Record<string, string> {
  const out: Record<string, string> = {}
  for (const f of PROV_FIELDS) {
    if (f.kind !== 'url') continue
    const v = String(form[f.key] ?? '').trim()
    if (v && !/^https?:\/\/[^\s/]+/i.test(v)) out[f.key] = 'An address starting with http:// or https://'
  }
  return out
}

/** IPTC digital source types that mean "made by AI", as the server reads them. */
export function digitalSourceText(v: unknown): string | null {
  if (typeof v !== 'string' || !v) return null
  const tail = v.split('/').pop() ?? v
  const words: Record<string, string> = {
    trainedAlgorithmicMedia: 'Made by generative AI',
    compositeWithTrainedAlgorithmicMedia: 'Edited with generative AI',
    algorithmicMedia: 'Made by an algorithm',
    digitalCapture: 'A camera capture',
    digitalArt: 'Digital art',
    composite: 'A composite',
    compositeSynthetic: 'A composite with synthetic parts',
  }
  return words[tail] ?? tail
}

export type SourceStatus = 'ok' | 'gone' | 'moved' | 'error' | null | undefined

export function sourceStatusText(s: SourceStatus): string {
  switch (s) {
    case 'ok':
      return 'Online'
    case 'gone':
      return 'Page gone (your copy is safe)'
    case 'moved':
      return 'Moved'
    case 'error':
      return 'Couldn’t check'
    default:
      return 'Not checked yet'
  }
}
