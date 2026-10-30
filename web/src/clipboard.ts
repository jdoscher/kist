/** Turn clipboard contents into things to add: files to upload, URLs to fetch. */

export type AddJob =
  | { kind: 'file'; file: File }
  // fallback: the clipboard's own copy of the image, used if the server
  // can't download the URL (expired signed links, hotlink protection…)
  | { kind: 'url'; url: string; fallback?: File }

interface Clip {
  files: File[]
  html: string
  text: string
}

const URL_RE = /^https?:\/\/\S+$/i

function named(f: File): File {
  if (f.name && f.name !== 'image.png') return f
  const ext = (f.type.split('/')[1] || 'png').replace('jpeg', 'jpg').replace('svg+xml', 'svg')
  return new File([f], `pasted-${Date.now()}.${ext}`, { type: f.type })
}

function htmlImageUrls(html: string): string[] {
  if (!html) return []
  const doc = new DOMParser().parseFromString(html, 'text/html')
  return [...doc.querySelectorAll('img')]
    .map((img) => img.getAttribute('src') ?? '')
    .filter((src) => URL_RE.test(src))
}

function textUrls(text: string): string[] {
  return text
    .split(/\s+/)
    .map((s) => s.trim())
    .filter((s) => URL_RE.test(s))
}

function toJobs({ files, html, text }: Clip): AddJob[] {
  const imgUrls = htmlImageUrls(html)
  // "Copy image" in a browser puts the pixels AND an <img src> on the
  // clipboard; the original file is better quality (and keeps GIF frames)
  if (files.length === 1 && imgUrls.length === 1) {
    return [{ kind: 'url', url: imgUrls[0], fallback: named(files[0]) }]
  }
  if (files.length) return files.map((f) => ({ kind: 'file', file: named(f) }))
  const urls = textUrls(text)
  if (urls.length) return [...new Set(urls)].map((url) => ({ kind: 'url', url }))
  return [...new Set(imgUrls)].map((url) => ({ kind: 'url', url }))
}

export function jobsFromDataTransfer(dt: DataTransfer | null): AddJob[] {
  if (!dt) return []
  const files: File[] = []
  for (const item of dt.items) {
    if (item.kind === 'file') {
      const f = item.getAsFile()
      if (f) files.push(f)
    }
  }
  return toJobs({ files, html: dt.getData('text/html'), text: dt.getData('text/plain') })
}

/** Async Clipboard API — only available in secure contexts (https/localhost). */
export function canReadClipboard(): boolean {
  return window.isSecureContext && typeof navigator.clipboard?.read === 'function'
}

export async function jobsFromClipboardApi(): Promise<AddJob[]> {
  const files: File[] = []
  let html = ''
  let text = ''
  for (const item of await navigator.clipboard.read()) {
    for (const type of item.types) {
      const blob = await item.getType(type)
      if (type === 'text/html') html = await blob.text()
      else if (type === 'text/plain') text = await blob.text()
      else if (type.startsWith('image/')) files.push(new File([blob], '', { type }))
    }
  }
  return toJobs({ files, html, text })
}

export function jobsFromText(text: string): AddJob[] {
  return [...new Set(textUrls(text))].map((url) => ({ kind: 'url', url }))
}

export function jobLabel(job: AddJob): string {
  if (job.kind === 'file') return job.file.name
  try {
    const u = new URL(job.url)
    const last = u.pathname.split('/').filter(Boolean).at(-1)
    return last ? `${u.hostname} · ${decodeURIComponent(last)}` : u.hostname
  } catch {
    return job.url
  }
}
