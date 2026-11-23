// The full-screen preview's info strip (D, or the Info button): where the
// item came from and what you've said about it, under the image. Off by
// default; the choice is remembered. No React: tests/previewInfo.test.ts.

import type { Item } from './api.ts'
import { creditLine } from './credits.ts'
import { linkableUrl } from './format.ts'

export const INFO_KEY = 'kist.previewInfo'

export interface InfoRow {
  label: string
  text: string
  /** a link (http/https only) */
  href?: string
}

type Store = Pick<Storage, 'getItem' | 'setItem'>

function browserStore(): Store | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function readInfoPref(store: Store | null = browserStore()): boolean {
  try {
    return store?.getItem(INFO_KEY) === '1'
  } catch {
    return false
  }
}

export function writeInfoPref(on: boolean, store: Store | null = browserStore()) {
  try {
    store?.setItem(INFO_KEY, on ? '1' : '0')
  } catch {
    // private mode: on for this visit only
  }
}

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

/** The rows the strip shows, empty ones left out. */
export function infoRows(item: Pick<Item, 'source_url' | 'provenance' | 'tags' | 'rating' | 'notes' | 'caption'>): InfoRow[] {
  const p = item.provenance ?? {}
  const rows: InfoRow[] = []
  // the page it was saved from beats the file's own URL (an image CDN)
  const page = linkableUrl(str(p.page_url) || null)
  const file = linkableUrl(item.source_url)
  const src = page ?? file
  if (src) {
    const title = str(p.page_title)
    rows.push({ label: 'Source', text: title ? `${title} (${src.host.replace(/^www\./, '')})` : src.host.replace(/^www\./, ''), href: src.href })
  }
  const credit = creditLine(p)
  if (credit) rows.push({ label: 'Credit', text: credit })
  if (item.tags.length) rows.push({ label: 'Tags', text: item.tags.join(', ') })
  // always shown, so an unrated item reads as unrated rather than missing
  const stars = Math.max(0, Math.min(5, Math.round(item.rating || 0)))
  rows.push({ label: 'Rating', text: stars ? '★'.repeat(stars) + '☆'.repeat(5 - stars) : '☆☆☆☆☆ not rated' })
  const caption = str(item.caption)
  if (caption) rows.push({ label: 'Caption', text: caption })
  const notes = str(item.notes)
  if (notes) rows.push({ label: 'Notes', text: notes })
  return rows
}
