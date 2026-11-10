// Tag namespaces (brand:rolex) and the vocabulary's implied tags, for
// display (no React; tests/tagNs.test.ts). The same rule as the server's
// vocabulary.split_namespace: a short word (letters, digits, _ . -), a
// colon, then a value that doesn't start with a space or a slash (so
// "http://x" isn't a namespace). Namespaces are lower case.

export interface TagParts {
  namespace: string | null
  value: string
}

const NAMESPACE = /^([A-Za-z0-9][A-Za-z0-9_.-]{0,31}):(\S.*)$/

export function splitTag(tag: string): TagParts {
  const m = NAMESPACE.exec(tag ?? '')
  if (!m || m[2].startsWith('/')) return { namespace: null, value: tag }
  return { namespace: m[1].toLowerCase(), value: m[2] }
}

/** "brand:*": every tag in a namespace (search syntax). */
export const namespaceWildcard = (ns: string) => `${ns}:*`

export function isWildcard(tag: string): boolean {
  return tag.endsWith(':*') && NAMESPACE.test(tag.slice(0, -1) + 'x')
}

/** Tags grouped by namespace (null = no namespace), namespaces sorted. */
export function groupByNamespace<T extends { name: string }>(tags: T[]): [string | null, T[]][] {
  const by = new Map<string | null, T[]>()
  for (const t of tags) {
    const ns = splitTag(t.name).namespace
    const k = ns ? ns.toLowerCase() : null
    if (!by.has(k)) by.set(k, [])
    by.get(k)!.push(t)
  }
  return [...by.entries()].sort(([a], [b]) => (a === null ? -1 : b === null ? 1 : a.localeCompare(b)))
}

/** For each implied tag on an item, which of its own tags implies it
 * (following chains: boots -> shoes -> footwear). */
export function impliedBy(
  tags: string[],
  implied: string[],
  rules: { tag: string; implied: string }[],
): Map<string, string> {
  const out = new Map<string, string>()
  const lower = (s: string) => s.toLowerCase()
  const next = new Map<string, string[]>()
  for (const r of rules) {
    const k = lower(r.tag)
    next.set(k, [...(next.get(k) ?? []), r.implied])
  }
  for (const own of tags) {
    const seen = new Set<string>([lower(own)])
    const queue = [...(next.get(lower(own)) ?? [])]
    while (queue.length) {
      const t = queue.shift()!
      if (seen.has(lower(t))) continue
      seen.add(lower(t))
      const hit = implied.find((i) => lower(i) === lower(t))
      if (hit && !out.has(hit)) out.set(hit, own)
      queue.push(...(next.get(lower(t)) ?? []))
    }
  }
  return out
}
