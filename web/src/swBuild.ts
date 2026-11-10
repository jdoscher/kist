// Build-time half of the service worker (W2.2): which files the worker
// precaches, the build's version (its cache name) and writing both into
// dist/sw.js. Used by the Vite plugin in vite.config.ts; no DOM, no Node
// APIs (tests/sw.test.ts).

export interface SwBuild {
  version: string
  assets: string[]
}

/** The marker in public/sw.js that the build replaces. */
export const BUILD_RE = /\/\*__KIST_BUILD__\*\/[\s\S]*?\/\*__KIST_BUILD_END__\*\//

/** Everything the app shell needs offline: the page itself ('/'), every
 * hashed file of the bundle (lazy screens too, so they open offline), and
 * the root files index.html links (icons, manifest, theme-init.js) with
 * their ?v= query, as the page asks for them. The legacy .woff fonts
 * (every browser that has service workers reads .woff2) and source maps
 * are left out. */
export function precacheList(bundleFiles: string[], indexHtml: string): string[] {
  const out = new Set<string>(['/'])
  for (const f of bundleFiles) {
    if (/\.(map|woff)$/.test(f) || f === 'index.html' || f === 'sw.js') continue
    if (!f.startsWith('assets/')) continue
    out.add(`/${f}`)
  }
  for (const m of indexHtml.matchAll(/\b(?:href|src)="(\/[^"]*)"/g)) {
    const url = m[1]
    if (url.startsWith('//')) continue
    out.add(url)
  }
  return [...out].sort((a, b) => (a === '/' ? -1 : b === '/' ? 1 : a.localeCompare(b)))
}

/** FNV-1a, 2 × 32 bits: a short stable id for the build. */
export function hashText(text: string): string {
  let h1 = 0x811c9dc5
  let h2 = 0x01000193 ^ 0x5bd1e995
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i)
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0
    h2 = Math.imul(h2 ^ c, 0x01000193 + 2) >>> 0
  }
  return h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0')
}

/** One version per build: the file list plus index.html and the root
 * files' content (asset names already carry their content hash). A build
 * that changes nothing gives the same version, so browsers see no update. */
export function buildVersion(assets: string[], indexHtml: string, extraContent = ''): string {
  return hashText(`${assets.join('\n')}\n--\n${indexHtml}\n--\n${extraContent}`).slice(0, 12)
}

export function injectBuild(swSource: string, build: SwBuild): string {
  if (!BUILD_RE.test(swSource)) throw new Error('sw.js has no /*__KIST_BUILD__*/ marker')
  return swSource.replace(BUILD_RE, `/*__KIST_BUILD__*/ ${JSON.stringify(build)} /*__KIST_BUILD_END__*/`)
}
