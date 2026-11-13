// Colour books (F5): the user's own colour lists (ASE, CSV, GPL) and the
// nearest code to a colour. API calls plus a few pure helpers
// (tests/colourBooks.test.ts). The server does the matching
// (kist/services/colourbooks.py, CIEDE2000).

import { request } from './api.ts'

export interface ColourBook {
  id: string
  name: string
  enabled: boolean
  format: 'ase' | 'csv' | 'gpl'
  source_filename: string | null
  imported_at: string
  count: number
}

export interface BookSwatch {
  code: string
  name: string
  hex: string
  lab?: [number, number, number]
  /** converted without a profile (CMYK, grey) or outside sRGB */
  approx?: boolean
}

export interface CodeMatch extends BookSwatch {
  book_id: string
  book: string
  /** CIEDE2000 difference from the colour asked about */
  de: number
}

export interface FoundSwatch extends BookSwatch {
  book_id: string
  book: string
  /** the colour search value: book:<book>/<code> */
  ref: string
}

export interface PaletteCode {
  hex: string
  pct: number
  name: string
  matches: CodeMatch[]
}

export interface ImportResult extends ColourBook {
  skipped: number
  warnings: string[]
}

export const cbApi = {
  list: () => request<ColourBook[]>('/api/colourbooks'),
  get: (id: string) => request<ColourBook & { swatches: BookSwatch[] }>(`/api/colourbooks/${encodeURIComponent(id)}`),
  import: (file: File, name?: string) => {
    const form = new FormData()
    form.append('file', file, file.name)
    if (name?.trim()) form.append('name', name.trim())
    return request<ImportResult>('/api/colourbooks', { method: 'POST', body: form }, 60_000)
  },
  patch: (id: string, change: { name?: string; enabled?: boolean }) =>
    request<ColourBook>(`/api/colourbooks/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(change),
    }),
  remove: (id: string) => request<void>(`/api/colourbooks/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  paletteCodes: (itemId: string) => request<PaletteCode[]>(`/api/items/${encodeURIComponent(itemId)}/palette/codes`),
  lookup: (q: string, limit = 12, signal?: AbortSignal) =>
    request<FoundSwatch[]>(`/api/colourbooks/lookup?${new URLSearchParams({ q, limit: String(limit) })}`, { signal }),
}

const REF = /^book:([^/]+)\/(.+)$/i

/** Whether a colour search value is a colour book code rather than a hex. */
export function isBookRef(value: string | null | undefined): boolean {
  return !!value && REF.test(value)
}

/** 'book:ral/RAL 7016' -> { book: 'ral', code: 'RAL 7016' } */
export function parseRef(value: string): { book: string; code: string } | null {
  const m = REF.exec(value)
  return m ? { book: m[1], code: m[2] } : null
}

export function refFor(bookId: string, code: string): string {
  return `book:${bookId}/${code}`
}

/** ΔE in words: under 1 is the same to the eye, under 2 only close up,
 * under 5 a near match, more is only the closest there is. */
export function deWords(de: number): string {
  if (de < 1) return 'same to the eye'
  if (de < 2) return 'very close'
  if (de < 5) return 'close'
  if (de < 10) return 'near'
  return 'closest in the book'
}

export function deText(de: number): string {
  return `ΔE ${de < 10 ? de.toFixed(1) : Math.round(de)}`
}

/** A swatch's label: "RAL 7016 Anthracite grey" (code first). */
export function swatchLabel(s: Pick<BookSwatch, 'code' | 'name'>): string {
  return s.name ? `${s.code} ${s.name}` : s.code
}

/** The accept attribute for the import field. */
export const BOOK_ACCEPT = '.ase,.csv,.gpl,.txt,text/csv'
