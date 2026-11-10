// Video/GIF scrubbing, clips and media facts (Milestone 5). Pure, tested
// in tests/m45.test.ts.

export interface ScrubInfo {
  frames: number
  cols: number
  rows: number
  frame_w: number
  frame_h: number
  times?: number[]
}

/** Which sprite frame a pointer at x (0..width across the tile) shows. */
export function scrubFrameIndex(x: number, width: number, frames: number): number {
  if (frames <= 1 || width <= 0 || !Number.isFinite(x)) return 0
  const f = Math.floor((x / width) * frames)
  return Math.max(0, Math.min(frames - 1, f))
}

/** CSS for showing one sprite frame scaled to cover a w×h tile. */
export function scrubFrameStyle(info: ScrubInfo, index: number, w: number, h: number) {
  const scale = Math.max(w / info.frame_w, h / info.frame_h)
  const fw = info.frame_w * scale
  const fh = info.frame_h * scale
  const col = index % info.cols
  const row = Math.floor(index / info.cols)
  const offX = (w - fw) / 2
  const offY = (h - fh) / 2
  return {
    backgroundSize: `${info.cols * fw}px ${info.rows * fh}px`,
    backgroundPosition: `${offX - col * fw}px ${offY - row * fh}px`,
  }
}

/** The time a frame shows (scrub.json's times, else evenly spread). */
export function scrubFrameTime(info: ScrubInfo, index: number, duration: number | null): number | null {
  if (info.times && info.times[index] != null) return info.times[index]
  if (!duration) return null
  return ((index + 0.5) / info.frames) * duration
}

export type ClipFormat = 'mp4' | 'gif' | 'webp' | 'mp3' | 'wav'

export const VIDEO_CLIP_FORMATS: [ClipFormat, string][] = [
  ['mp4', 'MP4 video'],
  ['gif', 'GIF'],
  ['webp', 'Animated WebP'],
]
export const AUDIO_CLIP_FORMATS: [ClipFormat, string][] = [
  ['mp3', 'MP3'],
  ['wav', 'WAV'],
]

/** Longest GIF/WebP clip the dialog allows (they get big fast). */
export const ANIMATED_MAX_S = 30

export function clipErrors(start: number, end: number, duration: number | null, format: ClipFormat): string[] {
  const out: string[] = []
  if (!Number.isFinite(start) || !Number.isFinite(end)) return ['Start and end must be numbers of seconds.']
  if (start < 0) out.push('Start can’t be before 0.')
  if (end <= start) out.push('End must be after the start.')
  if (duration != null && end > duration + 0.05) out.push(`End is past the end (${fmtTime(duration)}).`)
  if (end - start < 0.1 && end > start) out.push('A clip needs at least a tenth of a second.')
  if ((format === 'gif' || format === 'webp') && end - start > ANIMATED_MAX_S)
    out.push(`GIF and WebP clips are at most ${ANIMATED_MAX_S} seconds; use MP4 for longer.`)
  return out
}

/** m:ss.s (or h:mm:ss.s), for in/out points. */
export function fmtTime(s: number): string {
  if (!Number.isFinite(s) || s < 0) s = 0
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = (s % 60).toFixed(1).padStart(4, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/** "1:02.5", "62.5", "1:02:03" -> seconds; null when it isn't a time. */
export function parseTime(text: string): number | null {
  const t = text.trim()
  if (!t) return null
  const parts = t.split(':')
  if (parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return null
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0)
}

// ---------- media facts for the info panel ----------

export type Media = Record<string, unknown>

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

/** Rows of [label, value] for the media details panel. */
/**
 * "Taken" as the person saw the clock. A photo's EXIF time is the camera's
 * wall clock with no zone (kist/services/media.py stores it as if it were
 * UTC, and keeps the zone apart in exif.offset), so it is shown in UTC: an
 * iPhone shot at 6:13 in Los Angeles reads 6:13, not 23:13 the day before
 * in a browser on Pacific time. A video's time is a real UTC instant
 * (QuickTime creation_time), so it is shown in the browser's zone.
 */
export function takenText(takenAt: string, wallClock: boolean, locale?: string): string {
  const d = new Date(takenAt)
  if (Number.isNaN(d.getTime())) return takenAt
  return wallClock ? d.toLocaleString(locale, { timeZone: 'UTC' }) : d.toLocaleString(locale)
}

export function mediaRows(media: Media | null | undefined, takenAt?: string | null): [string, string][] {
  const out: [string, string][] = []
  if (!media) return out
  const exif = (media.exif ?? media.raw ?? {}) as Record<string, unknown>
  const camera = str(exif.camera) ?? ([str(exif.make), str(exif.model)].filter(Boolean).join(' ') || null)
  if (camera) out.push(['Camera', camera])
  if (str(exif.lens)) out.push(['Lens', str(exif.lens)!])
  const settings: string[] = []
  if (num(exif.focal_length)) settings.push(`${trim(num(exif.focal_length)!)} mm`)
  if (num(exif.aperture)) settings.push(`ƒ/${trim(num(exif.aperture)!)}`)
  const shutter = str(exif.shutter_text) ?? (num(exif.shutter) ? shutterText(num(exif.shutter)!) : null)
  if (shutter) settings.push(`${shutter} s`)
  if (num(exif.iso)) settings.push(`ISO ${num(exif.iso)}`)
  if (settings.length) out.push(['Exposure', settings.join(' · ')])
  if (takenAt) out.push(['Taken', takenText(takenAt, !!str(exif.taken_at))])
  const v = (media.video ?? null) as Record<string, unknown> | null
  const a = (media.audio ?? null) as Record<string, unknown> | null
  const av = v ?? a
  if (av) {
    if (num(av.duration)) out.push(['Duration', fmtTime(num(av.duration)!)])
    if (v && num(v.fps)) out.push(['Frame rate', `${trim(num(v.fps)!)} fps`])
    const codec = [str(v?.codec), str(av.audio_codec)].filter(Boolean).join(' + ')
    if (codec) out.push(['Codec', codec])
    if (num(av.bitrate)) out.push(['Bit rate', `${Math.round(num(av.bitrate)! / 1000)} kb/s`])
    if (v) out.push(['Sound', v.has_audio ? 'Yes' : 'No'])
    if (a && str(a.artist)) out.push(['Artist', str(a.artist)!])
    if (a && str(a.title)) out.push(['Title', str(a.title)!])
  }
  const anim = media.animation as Record<string, unknown> | undefined
  if (anim && num(anim.frames)) out.push(['Frames', String(num(anim.frames))])
  const model = media.model as Record<string, unknown> | undefined
  if (model) {
    if (num(model.triangles)) out.push(['Triangles', num(model.triangles)!.toLocaleString()])
    const size = model.size as number[] | undefined
    if (Array.isArray(size) && size.length === 3) out.push(['Size', size.map((n) => trim(n)).join(' × ')])
    if (str(model.format)) out.push(['Format', str(model.format)!.toUpperCase()])
  }
  const bundle = media.bundle as Record<string, unknown> | undefined
  if (bundle && num(bundle.count)) out.push(['Files inside', String(num(bundle.count))])
  return out
}

function trim(n: number): string {
  return String(Math.round(n * 10) / 10)
}

export function shutterText(s: number): string {
  if (s >= 1) return trim(s)
  return `1/${Math.round(1 / s)}`
}

/** GPS from the media block: {lat, lon} or null. */
export function mediaGps(media: Media | null | undefined): { lat: number; lon: number } | null {
  const exif = (media?.exif ?? media?.raw ?? null) as Record<string, unknown> | null
  const lat = num(exif?.lat)
  const lon = num(exif?.lon)
  return lat !== null && lon !== null ? { lat, lon } : null
}

export function fmtLatLon(lat: number, lon: number): string {
  const f = (v: number, pos: string, neg: string) => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}`
  return `${f(lat, 'N', 'S')}, ${f(lon, 'E', 'W')}`
}

export function playableOf(media: Media | null | undefined): 'native' | 'proxy' | 'none' | null {
  const p = media?.playable
  return p === 'native' || p === 'proxy' || p === 'none' ? p : null
}

export function scrubOf(media: Media | null | undefined): ScrubInfo | null {
  const s = media?.scrub as ScrubInfo | undefined
  if (!s || !num(s.frames) || !num(s.cols) || !num(s.rows) || !num(s.frame_w) || !num(s.frame_h)) return null
  return s
}

export function svgColors(media: Media | null | undefined): string[] {
  const svg = media?.svg as { colors?: { hex: string }[] } | undefined
  return (svg?.colors ?? []).map((c) => c.hex).filter((h) => /^#[0-9a-f]{3,8}$/i.test(h))
}

/** Peaks as bar heights 0..1 (BBC audiowaveform: min,max pairs). */
export function waveformBars(data: number[], bits: number, bars: number): number[] {
  const pairs = Math.floor(data.length / 2)
  if (!pairs || bars <= 0) return []
  const full = bits === 16 ? 32768 : 128
  const out: number[] = []
  for (let b = 0; b < bars; b++) {
    const from = Math.floor((b * pairs) / bars)
    const to = Math.max(from + 1, Math.floor(((b + 1) * pairs) / bars))
    let peak = 0
    for (let i = from; i < to && i < pairs; i++) peak = Math.max(peak, Math.abs(data[2 * i]), Math.abs(data[2 * i + 1]))
    out.push(Math.min(1, peak / full))
  }
  return out
}

