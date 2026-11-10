// The timeline (/timeline) and On this day (Milestone 5): bucket labels
// and the date range a bucket jumps to. Keys come from GET /api/timeline:
// YYYY (year), YYYY-MM (month), YYYY-MM-DD (day). Pure, tested in
// tests/m45.test.ts.

export type TimelineGroup = 'day' | 'month' | 'year'

export interface Bucket {
  key: string
  count: number
  first_item_id: string | null
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

export function parseKey(key: string): { y: number; m: number | null; d: number | null } | null {
  const m = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(key)
  if (!m) return null
  return { y: Number(m[1]), m: m[2] ? Number(m[2]) : null, d: m[3] ? Number(m[3]) : null }
}

/** "2024", "June 2024", "Friday 21 June 2024". */
export function bucketLabel(key: string): string {
  const p = parseKey(key)
  if (!p) return key
  if (p.m === null) return String(p.y)
  if (p.d === null) return `${MONTHS[p.m - 1]} ${p.y}`
  const wd = new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay()
  return `${DAYS[wd]} ${p.d} ${MONTHS[p.m - 1]} ${p.y}`
}

/** The heading a bucket sits under: the year for months, the month for days. */
export function bucketSection(key: string): string {
  const p = parseKey(key)
  if (!p || p.m === null) return ''
  if (p.d === null) return String(p.y)
  return `${MONTHS[p.m - 1]} ${p.y}`
}

const pad = (n: number) => String(n).padStart(2, '0')

/** The dates a bucket covers, as the date_after / date_before filters
 * (inclusive days, YYYY-MM-DD). */
export function bucketRange(key: string): { after: string; before: string } | null {
  const p = parseKey(key)
  if (!p) return null
  if (p.m === null) return { after: `${p.y}-01-01`, before: `${p.y}-12-31` }
  if (p.d === null) {
    const last = new Date(Date.UTC(p.y, p.m, 0)).getUTCDate()
    return { after: `${p.y}-${pad(p.m)}-01`, before: `${p.y}-${pad(p.m)}-${pad(last)}` }
  }
  const day = `${p.y}-${pad(p.m)}-${pad(p.d)}`
  return { after: day, before: day }
}

/** Buckets grouped under their section headings, newest first, keeping
 * the server's order inside each. */
export function groupBuckets(buckets: Bucket[]): { section: string; total: number; buckets: Bucket[] }[] {
  const out: { section: string; total: number; buckets: Bucket[] }[] = []
  for (const b of buckets) {
    const s = bucketSection(b.key)
    let g = out[out.length - 1]
    if (!g || g.section !== s) {
      g = { section: s, total: 0, buckets: [] }
      out.push(g)
    }
    g.buckets.push(b)
    g.total += b.count
  }
  return out
}

/** "3 years ago", "Last year". */
export function yearsAgoText(n: number): string {
  if (n <= 0) return 'This year'
  if (n === 1) return 'A year ago'
  return `${n} years ago`
}

/** Local YYYY-MM-DD for today (On this day asks for the viewer's date). */
export function localDay(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** A bar's width (0..100) for a count against the largest bucket. */
export function barPct(count: number, max: number): number {
  if (max <= 0 || count <= 0) return 0
  return Math.max(2, Math.round((count / max) * 100))
}
