// Bulk rename patterns (Milestone 3), no React (tests/rename.test.ts).
// The server is the authority (POST /api/items/rename with dry_run); this
// mirrors it so the preview changes as you type, and explains mistakes.
//
//   {name} {ext} {n} {n:3} {date:%Y%m%d} {w} {h} {folder} {tag:ns}

export interface RenameToken {
  token: string
  example: string
  help: string
}

export const RENAME_TOKENS: RenameToken[] = [
  { token: '{name}', example: 'IMG_0042', help: 'the current name without its extension' },
  { token: '{n:3}', example: '001', help: 'a counter, padded to 3 digits ({n} for no padding)' },
  { token: '{date:%Y%m%d}', example: '20261109', help: 'when it was added (strftime: %Y %m %d %H %M %S)' },
  { token: '{w}x{h}', example: '1920x1080', help: 'width and height in pixels' },
  { token: '{folder}', example: 'Moodboards', help: 'its first folder' },
  { token: '{tag:brand}', example: 'acme', help: 'the value of a namespaced tag (brand:acme)' },
  { token: '{ext}', example: 'jpg', help: 'the extension (added for you when the pattern has none)' },
]

const KNOWN = /^(name|ext|n(:\d{1,2})?|date(:.+)?|w|h|folder|tag:[^{}]+)$/

/** Problems with a pattern, in words (empty when it's fine). */
export function patternErrors(pattern: string): string[] {
  const errors: string[] = []
  if (!pattern.trim()) errors.push('Type a pattern, e.g. {name}_{n:3}.')
  let depth = 0
  for (const ch of pattern) {
    if (ch === '{') depth++
    if (ch === '}') depth--
    if (depth < 0 || depth > 1) {
      errors.push('Braces don’t match: every { needs a }.')
      return errors
    }
  }
  if (depth !== 0) {
    errors.push('Braces don’t match: every { needs a }.')
    return errors
  }
  for (const m of pattern.matchAll(/\{([^{}]*)\}/g)) {
    if (!KNOWN.test(m[1])) errors.push(`Unknown placeholder {${m[1]}}.`)
  }
  if (pattern.includes('/')) errors.push('A name can’t contain “/”.')
  return errors
}

export interface RenameSource {
  filename: string
  ext: string
  width: number | null
  height: number | null
  created_at: string
  tags: string[]
  folder?: string | null
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/** strftime's common codes, in UTC (the server's dates are UTC). */
export function strftime(fmt: string, d: Date): string {
  const map: Record<string, string> = {
    Y: String(d.getUTCFullYear()),
    y: pad2(d.getUTCFullYear() % 100),
    m: pad2(d.getUTCMonth() + 1),
    d: pad2(d.getUTCDate()),
    H: pad2(d.getUTCHours()),
    M: pad2(d.getUTCMinutes()),
    S: pad2(d.getUTCSeconds()),
    '%': '%',
  }
  return fmt.replace(/%(.)/g, (all, c: string) => map[c] ?? all)
}

/** The name without its last extension (as the server splits it). */
function stem(filename: string): string {
  if (filename.startsWith('.') || !filename.replace(/^\.+|\.+$/g, '').includes('.')) return filename
  return filename.slice(0, filename.lastIndexOf('.'))
}

/** One item's new name under `pattern`, `n` being its counter value.
 * The extension is kept when the pattern doesn't place it. */
export function renameOne(pattern: string, src: RenameSource, n: number): string {
  let usedExt = false
  const out = pattern.replace(/\{([^{}]*)\}/g, (all, body: string) => {
    if (body === 'name') return stem(src.filename)
    if (body === 'ext') {
      usedExt = true
      return src.ext
    }
    if (body === 'n') return String(n)
    const counter = body.match(/^n:(\d{1,2})$/)
    if (counter) return String(n).padStart(Math.min(12, Number(counter[1])), '0')
    if (body.startsWith('date')) return strftime(body.slice(5) || '%Y%m%d', new Date(src.created_at))
    if (body === 'w') return String(src.width ?? 0)
    if (body === 'h') return String(src.height ?? 0)
    if (body === 'folder') return src.folder ?? ''
    if (body.startsWith('tag:')) {
      const ns = body.slice(4).toLowerCase()
      const t = src.tags.find((x) => x.toLowerCase().startsWith(`${ns}:`))
      return t ? t.slice(ns.length + 1) : ''
    }
    return all
  })
  const clean = out.replace(/\//g, '-').trim()
  return usedExt || !src.ext ? clean : `${clean}.${src.ext}`
}

/** Names for a selection in order, with clashes numbered " (2)", " (3)"
 * the way the server does it. */
export function renameAll(pattern: string, items: RenameSource[], start = 1): string[] {
  const seen = new Map<string, number>()
  return items.map((it, i) => {
    const name = renameOne(pattern, it, start + i)
    const key = name.toLowerCase()
    const count = seen.get(key) ?? 0
    seen.set(key, count + 1)
    if (!count) return name
    const dot = name.lastIndexOf('.')
    return dot > 0 ? `${name.slice(0, dot)} (${count + 1})${name.slice(dot)}` : `${name} (${count + 1})`
  })
}
