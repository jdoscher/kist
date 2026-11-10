// Export presets (Milestone 4): resized/converted copies or a contact
// sheet. The form as the preset object POST /api/items/export-images and
// /api/export-presets take. Pure, tested in tests/m45.test.ts.

export type ImageFormat = 'jpg' | 'webp' | 'png' | 'avif'
export const WATERMARK_POSITIONS = ['center', 'bottom-right', 'bottom-left', 'top-right', 'top-left'] as const

export interface PresetForm {
  name: string
  maxSide: string // '' = full size
  format: ImageFormat
  quality: number
  stripMetadata: boolean
  watermark: boolean
  wmText: string
  wmPosition: string
  wmOpacity: number
  contactSheet: boolean
  cols: number
  caption: 'filename' | 'none'
}

export interface PresetObject {
  name?: string
  max_side: number | null
  format: ImageFormat
  quality: number
  strip_metadata: boolean
  watermark?: { text: string; position: string; opacity: number } | null
  contact_sheet?: { cols: number; caption: string } | null
}

export function presetToForm(p: Partial<PresetObject> & { name?: string }): PresetForm {
  return {
    name: p.name ?? '',
    maxSide: p.max_side ? String(p.max_side) : '',
    format: (p.format as ImageFormat) ?? 'jpg',
    quality: p.quality ?? 85,
    stripMetadata: p.strip_metadata ?? true,
    watermark: !!p.watermark,
    wmText: p.watermark?.text ?? '',
    wmPosition: p.watermark?.position ?? 'bottom-right',
    wmOpacity: p.watermark?.opacity ?? 0.35,
    contactSheet: !!p.contact_sheet,
    cols: p.contact_sheet?.cols ?? 4,
    caption: p.contact_sheet?.caption === 'none' ? 'none' : 'filename',
  }
}

export function presetErrors(f: PresetForm): string[] {
  const out: string[] = []
  if (f.maxSide !== '') {
    const n = Number(f.maxSide)
    if (!Number.isInteger(n) || n < 16 || n > 16384) out.push('Longest side: a whole number from 16 to 16384, or empty for full size.')
  }
  if (!(f.quality >= 1 && f.quality <= 100)) out.push('Quality is 1 to 100.')
  if (f.watermark && !f.wmText.trim()) out.push('Type the watermark text, or turn the watermark off.')
  if (f.contactSheet && !(f.cols >= 1 && f.cols <= 12)) out.push('A contact sheet has 1 to 12 columns.')
  return out
}

export function formToPreset(f: PresetForm, withName = false): PresetObject {
  const p: PresetObject = {
    max_side: f.maxSide === '' ? null : Number(f.maxSide),
    format: f.format,
    quality: Math.round(f.quality),
    strip_metadata: f.stripMetadata,
    watermark: f.watermark ? { text: f.wmText.trim(), position: f.wmPosition, opacity: Math.round(f.wmOpacity * 100) / 100 } : null,
    contact_sheet: f.contactSheet ? { cols: Math.round(f.cols), caption: f.caption } : null,
  }
  if (withName) p.name = f.name.trim()
  return p
}

export function presetSummary(p: Partial<PresetObject>): string {
  const parts: string[] = []
  if (p.contact_sheet) parts.push(`contact sheet, ${p.contact_sheet.cols} across (PDF)`)
  else {
    parts.push(p.max_side ? `${p.max_side} px` : 'full size')
    parts.push((p.format ?? 'jpg').toUpperCase())
    if (p.format !== 'png') parts.push(`quality ${p.quality ?? 85}`)
  }
  if (p.watermark) parts.push(`watermark “${p.watermark.text}”`)
  parts.push(p.strip_metadata === false ? 'keeps metadata (not GPS)' : 'metadata removed')
  return parts.join(' · ')
}
