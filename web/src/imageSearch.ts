// Search by an image that isn't in the library: the file is sent to
// POST /api/search/image and never stored. The query (and the URL) carry
// only a short id for it, so Back and Forward work within this tab; after
// a reload the image is gone and the grid says to drop it again.
// No React; tests/search.test.ts.

const images = new Map<string, Blob>()
let seq = 0

/** Keep an image for searching; returns its id for Query.image. */
export function registerImage(blob: Blob, now = Date.now()): string {
  const id = `img${now.toString(36)}${(seq++).toString(36)}`
  images.set(id, blob)
  // keep the last few (Back to an older image search still works)
  while (images.size > 8) images.delete(images.keys().next().value as string)
  return id
}

export function imageFor(id: string): Blob | undefined {
  return images.get(id)
}

/** A thumbnail URL for the search-bar chip (revoked by the caller). */
export function imagePreviewUrl(id: string): string | null {
  const b = images.get(id)
  return b && typeof URL !== 'undefined' && URL.createObjectURL ? URL.createObjectURL(b) : null
}

/** The first image in a drop or paste, if any. */
export function firstImage(files: ArrayLike<File> | null | undefined): File | null {
  if (!files) return null
  for (let i = 0; i < files.length; i++) {
    const f = files[i]
    if (f && f.type.startsWith('image/')) return f
  }
  return null
}
