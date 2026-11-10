// The /add page (the bookmarklet's confirm card) and the /bookmarklet
// generator. Parameters come from any web page, so everything is checked
// here: only http(s) URLs, length caps, never HTML. Spec:
// kist-ext docs/add-route.md. Pure, tested in tests/m45.test.ts.

export const ADD_URL_MAX = 2000
export const ADD_TITLE_MAX = 300
export const ADD_ALT_MAX = 4

export interface AddParams {
  /** the image to save; null when missing or refused */
  url: string | null
  /** other large images on the page (≤ 4) */
  alts: string[]
  page: string | null
  title: string
  /** 1 when the bookmarklet navigated this tab (pop-up blocked) */
  returnToPage: boolean
  /** the bookmarklet protocol version; unknown versions are read the same way */
  version: string | null
}

/** An http(s) URL under the cap, normalised, else null. */
export function safeHttpUrl(raw: string | null | undefined, max = ADD_URL_MAX): string | null {
  if (!raw || raw.length > max) return null
  let u: URL
  try {
    u = new URL(raw.trim())
  } catch {
    return null
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
  if (!u.hostname) return null
  return u.href
}

/** Text for display: control characters out, length capped. */
export function plainText(raw: string | null | undefined, max = ADD_TITLE_MAX): string {
  if (!raw || raw.length > max) return ''
  // eslint-disable-next-line no-control-regex
  return raw.replace(/[\u0000-\u001f\u007f]+/g, ' ').trim()
}

export function parseAddParams(search: string): AddParams {
  const s = new URLSearchParams(search)
  const url = safeHttpUrl(s.get('url'))
  const alts: string[] = []
  for (const a of s.getAll('alt')) {
    const u = safeHttpUrl(a)
    if (u && u !== url && !alts.includes(u)) alts.push(u)
    if (alts.length >= ADD_ALT_MAX) break
  }
  return {
    url,
    alts,
    page: safeHttpUrl(s.get('page')),
    title: plainText(s.get('title')),
    returnToPage: s.get('return') === '1',
    version: s.get('v'),
  }
}

export function hostOf(url: string | null): string {
  if (!url) return ''
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** The POST /api/items body the card sends (spec step 4). */
export function addPayload(
  p: AddParams,
  chosen: string,
  extra: { tags: string[]; folderIds: string[]; notes: string },
  now: Date = new Date(),
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    url: chosen,
    image_url: chosen,
    captured_at: now.toISOString(),
    method: 'bookmarklet',
  }
  if (p.page) {
    body.source_url = p.page
    body.page_url = p.page
  }
  if (p.title) body.title = p.title
  if (extra.tags.length) body.tags = extra.tags
  if (extra.folderIds.length) body.folder_ids = extra.folderIds
  if (extra.notes.trim()) body.notes = extra.notes.trim()
  return body
}

/** What a failed save says (spec step 5), in plain words. */
export function addErrorText(status: number, detail: string): string {
  if (status === 413) return 'That image is larger than Kist accepts.'
  if (status === 422 || status === 502)
    return `Kist couldn’t download that image${detail ? `: ${detail}` : ''}. Save it with the browser extension instead, which fetches it as you.`
  if (status === 0) return 'No connection to Kist. Check the network and try again.'
  return `Kist couldn’t save it${detail ? `: ${detail}` : ''}.`
}

// ---------- the bookmarklet generator ----------

/** The bookmarklet's source (kist-ext bookmarklet/bookmarklet.js, minified
 * the same way as its make.mjs). "__KIST__" becomes this Kist's address. */
export const BOOKMARKLET_SRC = `(function (K) {
var d = document;
function abs(u) {
try { var x = new URL(u, d.baseURI); return /^https?:$/.test(x.protocol) ? x.href : ""; } catch (e) { return ""; }
}
var seen = {}, list = [];
function add(u, area) {
u = abs(u);
if (u && !seen[u] && u.length < 2000) { seen[u] = 1; list.push({ u: u, a: area }); }
}
var og = d.querySelector('meta[property="og:image:secure_url"],meta[property="og:image"],meta[name="twitter:image"]');
if (og) add(og.getAttribute("content"), Infinity);
for (var i = 0; i < d.images.length; i++) {
var im = d.images[i];
var a = (im.naturalWidth || 0) * (im.naturalHeight || 0);
if (a >= 10000) add(im.currentSrc || im.src, a);
}
if (!list.length) { alert("Kist: no image on this page to save."); return; }
list.sort(function (x, y) { return y.a - x.a; });
var q = "url=" + encodeURIComponent(list[0].u);
for (var j = 1; j < list.length && j < 5; j++) q += "&alt=" + encodeURIComponent(list[j].u);
q += "&page=" + encodeURIComponent(location.href) + "&title=" + encodeURIComponent((d.title || "").slice(0, 300)) + "&v=1";
var w = window.open(K + "/add?" + q, "kist-add", "popup,width=460,height=640");
if (!w) location.href = K + "/add?" + q + "&return=1";
})("__KIST__");`

/** A Kist address as the bookmarklet wants it: origin + path, no trailing
 * slash; https:// assumed when missing; '' when it isn't a URL. */
export function normalizeKistBase(raw: string): string {
  let s = String(raw || '').trim()
  if (!s) return ''
  if (!/^https?:\/\//i.test(s)) s = `https://${s}`
  try {
    const u = new URL(s)
    return (u.origin + u.pathname).replace(/\/+$/, '')
  } catch {
    return ''
  }
}

/** The javascript: URL to drag to the bookmarks bar ('' for no address). */
export function bookmarkletFor(kistUrl: string, src: string = BOOKMARKLET_SRC): string {
  const base = normalizeKistBase(kistUrl)
  if (!base) return ''
  return `javascript:${encodeURIComponent(src.replace('"__KIST__"', JSON.stringify(base)))}`
}
