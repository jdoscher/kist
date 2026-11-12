// WCAG contrast maths and the colour pairs each theme must pass. Used by
// tests/contrast.test.ts and scripts/contrast.ts (which prints the table
// in docs/brand.md). Pure: no DOM.

export type Rgb = [number, number, number]

export function parseHex(hex: string): Rgb {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? [...h].map((c) => c + c).join('') : h.slice(0, 6)
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as Rgb
}

/** WCAG 2 relative luminance. */
export function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

export function contrast(a: string, b: string): number {
  const la = luminance(parseHex(a))
  const lb = luminance(parseHex(b))
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** `[data-theme='x'] { --name: #hex; }` blocks of themes.css -> tokens per theme. */
export function parseThemes(css: string): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {}
  const block = /([^{}]+)\{([^{}]*)\}/g
  let m: RegExpExecArray | null
  while ((m = block.exec(css))) {
    const names = [...m[1].matchAll(/data-theme='([a-z]+)'/g)].map((x) => x[1])
    if (!names.length) continue
    const vars: Record<string, string> = {}
    for (const v of m[2].matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) vars[v[1]] = v[2]
    for (const n of names) out[n] = { ...(out[n] ?? {}), ...vars }
  }
  return out
}

/** [foreground, background, minimum, what it is]. 4.5 = text (1.4.3),
 * 3 = UI parts, focus rings and large text (1.4.11, 2.4.13). */
export const PAIRS: [string, string, number, string][] = [
  ['text', 'bg', 4.5, 'body text'],
  ['text', 'bg-raised', 4.5, 'text on panels'],
  ['text', 'bg-hover', 4.5, 'text on buttons'],
  ['text', 'bg-active', 4.5, 'text on pressed buttons'],
  ['text-dim', 'bg', 4.5, 'secondary text'],
  ['text-dim', 'bg-raised', 4.5, 'secondary text on panels'],
  ['text-dim', 'bg-hover', 4.5, 'secondary text on hover'],
  ['text', 'chrome', 4.5, 'text in the top bar and side panels'],
  ['text-dim', 'chrome', 4.5, 'secondary text in the top bar and side panels'],
  ['accent', 'chrome', 4.5, 'links in the top bar and side panels'],
  ['accent', 'bg', 4.5, 'links'],
  ['accent', 'bg-raised', 4.5, 'links on panels'],
  ['on-accent', 'accent', 4.5, 'primary button label'],
  ['on-accent', 'accent-hover', 4.5, 'primary button label, hover'],
  ['text', 'accent-soft', 4.5, 'selected row / chip text'],
  ['soft-dim', 'accent-soft', 4.5, 'counts on a selected row'],
  ['accent', 'accent-soft', 3, 'selected-row bar'],
  ['field-border', 'bg', 3, 'field outline'],
  ['field-border', 'bg-raised', 3, 'field outline on panels'],
  ['focus', 'bg', 3, 'focus ring'],
  ['focus', 'bg-raised', 3, 'focus ring on panels'],
  ['star', 'bg-raised', 3, 'rating stars'],
  ['danger', 'bg', 4.5, 'error text'],
  ['danger', 'bg-raised', 4.5, 'error text on panels'],
  ['danger-on', 'danger-bg', 4.5, 'server banner'],
  ['success', 'bg-raised', 3, 'done marker'],
  ['warn', 'bg-raised', 3, 'warning icon'],
  ['info', 'bg-raised', 4.5, 'info text'],
  ['canvas-text', 'canvas', 4.5, 'grid heading on the canvas'],
  ['canvas-dim', 'canvas', 4.5, 'grid counts on the canvas'],
  ['canvas-focus', 'canvas', 3, 'tile focus ring'],
  ['text', 'bg-float', 4.5, 'text on menus, dialogs, palette'],
  ['text-dim', 'bg-float', 4.5, 'secondary text on menus and dialogs'],
  ['accent', 'bg-float', 4.5, 'links on menus and dialogs'],
  ['focus', 'bg-float', 3, 'focus ring on menus and dialogs'],
  ['field-border', 'bg-float', 3, 'field outline in dialogs'],
  ['danger', 'bg-float', 4.5, 'error text in dialogs'],
  ['text', 'float-hover', 4.5, 'hovered / active menu row'],
  ['text-dim', 'float-hover', 4.5, 'secondary text on a hovered menu row'],
  ['accent', 'float-hover', 3, 'active-row bar in the palette'],
  ['preview-text', 'preview-bg', 4.5, 'text in the full-screen preview'],
  ['preview-dim', 'preview-bg', 4.5, 'secondary text in the preview'],
  ['preview-accent', 'preview-bg', 4.5, 'links and focus ring in the preview'],
  ['preview-text', 'preview-raised', 4.5, 'preview buttons'],
  ['preview-text', 'preview-hover', 4.5, 'preview buttons, hover'],
  ['preview-text', 'preview-active', 4.5, 'preview buttons, pressed'],
  ['preview-accent', 'preview-raised', 3, 'focus ring on preview buttons'],
  ['preview-line', 'preview-bg', 3, 'field outline in the preview'],
  ['preview-on-accent', 'preview-accent', 4.5, 'primary button in the preview'],
  ['preview-text', 'preview-panel', 4.5, 'notes panel text'],
  ['preview-dim', 'preview-panel', 4.5, 'notes panel secondary text'],
  ['preview-danger', 'preview-panel', 4.5, 'error text in the notes panel'],
  ['preview-text', 'preview-soft', 4.5, 'selected chip in the preview'],
  ['preview-soft-dim', 'preview-soft', 4.5, 'counts on a selected chip in the preview'],
]

export interface Check {
  theme: string
  fg: string
  bg: string
  ratio: number
  min: number
  what: string
  ok: boolean
}

export function checkThemes(themes: Record<string, Record<string, string>>): Check[] {
  const rows: Check[] = []
  for (const [theme, t] of Object.entries(themes)) {
    for (const [fg, bg, min, what] of PAIRS) {
      if (!t[fg] || !t[bg]) throw new Error(`${theme}: missing --${!t[fg] ? fg : bg}`)
      const ratio = contrast(t[fg], t[bg])
      rows.push({ theme, fg, bg, ratio, min, what, ok: ratio >= min })
    }
  }
  return rows
}
