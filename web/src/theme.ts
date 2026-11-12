// Appearance: colour theme and grid density. Pure resolution logic (tested
// in tests/theme.test.ts) plus applyAppearance(), which writes the result
// onto <html> as data-theme / data-density. The colours themselves live in
// themes.css; public/theme-init.js does the same first paint before React
// loads (keep the two in step).

export type ThemePref = 'system' | 'light' | 'dark' | 'gallery'
export type Theme = 'light' | 'dark' | 'gallery'
export type Density = 'compact' | 'comfortable' | 'gallery'

export const THEME_PREFS: { value: ThemePref; label: string; hint: string }[] = [
  { value: 'system', label: 'System', hint: 'Follows your device' },
  { value: 'light', label: 'Light', hint: '' },
  { value: 'dark', label: 'Dark', hint: '' },
  { value: 'gallery', label: 'Gallery grey', hint: 'Neutral mid-grey for judging colour' },
]

export const DENSITIES: { value: Density; label: string; hint: string }[] = [
  { value: 'compact', label: 'Compact', hint: 'Tight grid for sorting' },
  { value: 'comfortable', label: 'Comfortable', hint: '' },
  { value: 'gallery', label: 'Gallery', hint: 'Bigger tiles, sidebars tucked away' },
]

export function isThemePref(v: unknown): v is ThemePref {
  return v === 'system' || v === 'light' || v === 'dark' || v === 'gallery'
}

export function isDensity(v: unknown): v is Density {
  return v === 'compact' || v === 'comfortable' || v === 'gallery'
}

/** The theme to show: an explicit choice wins, System follows the OS. */
export function resolveTheme(pref: ThemePref, systemDark: boolean): Theme {
  if (pref === 'system') return systemDark ? 'dark' : 'light'
  return pref
}

/** Browser chrome colour (meta theme-color, the phone status bar) per
 * theme: each theme's --chrome, so the status bar and the top bar are one
 * surface (tests/theme.test.ts checks them against themes.css). */
export const THEME_COLOR: Record<Theme, string> = {
  dark: '#1C1C1A',
  light: '#FFFFFF',
  gallery: '#5C5C5C',
}

/** The page colour before the stylesheet paints: each theme's --bg.
 * public/theme-init.js sets the same on <html> at first paint, so a
 * gallery or light launch never flashes the dark #131312. */
export const THEME_BG: Record<Theme, string> = {
  dark: '#131312',
  light: '#FAF9F6',
  gallery: '#5C5C5C',
}

/** Grid spacing per density. `gap` is the space between tiles (px) and must
 * match --tile-gap in styles.css; `scale` multiplies the tile-size slider. */
export const DENSITY_GRID: Record<Density, { gap: number; scale: number }> = {
  compact: { gap: 2, scale: 1 },
  comfortable: { gap: 8, scale: 1 },
  gallery: { gap: 12, scale: 1.35 },
}

export function densityGap(d: Density): number {
  return DENSITY_GRID[d].gap
}

/** The tile size the grid lays out with, from the slider value. */
export function densityTileSize(size: number, d: Density): number {
  return Math.round(size * DENSITY_GRID[d].scale)
}

const DARK_QUERY = '(prefers-color-scheme: dark)'

export function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(DARK_QUERY).matches
}

/** Write the appearance onto <html> and the theme-color metas. */
export function applyAppearance(pref: ThemePref, density: Density, doc: Document = document) {
  const theme = resolveTheme(pref, systemPrefersDark())
  const root = doc.documentElement
  root.dataset.theme = theme
  root.dataset.density = density
  root.style.colorScheme = theme === 'light' ? 'light' : 'dark'
  root.style.backgroundColor = THEME_BG[theme]
  // two metas (one per OS scheme) follow the OS; an override pins both
  for (const meta of doc.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    const fallback = meta.dataset.default ?? meta.content
    meta.dataset.default = fallback
    meta.content = pref === 'system' ? fallback : THEME_COLOR[theme]
  }
}

/** Re-apply when the OS switches light/dark (only matters on System). */
export function watchSystemTheme(current: () => { theme: ThemePref; density: Density }) {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const mq = window.matchMedia(DARK_QUERY)
  const onChange = () => {
    const { theme, density } = current()
    if (theme === 'system') applyAppearance(theme, density)
  }
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}
