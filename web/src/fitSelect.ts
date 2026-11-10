// The phone toolbar's Type / Rating / Sort selects (Kist 2.0 M6c): each is
// as wide as the option it shows, so "Type", "Rating" and "Newest" read in
// full on a 390 px phone instead of "T…", "R…", "N…" (three equal 61 px
// boxes left 31 px for text). Pure; tests/fitSelect.test.ts. TopBar.tsx
// measures the text with the select's own font (useFitSelect).

export interface BoxChrome {
  paddingLeft: number
  paddingRight: number
  borderLeft: number
  borderRight: number
}

/** A select's border-box width for a label `textWidth` px wide. */
export function fitSelectWidth(textWidth: number, c: BoxChrome): number {
  if (!Number.isFinite(textWidth) || textWidth < 0) textWidth = 0
  return Math.ceil(textWidth + c.paddingLeft + c.paddingRight + c.borderLeft + c.borderRight)
}

/** The chrome from getComputedStyle (px strings). */
export function chromeOf(cs: { paddingLeft: string; paddingRight: string; borderLeftWidth: string; borderRightWidth: string }): BoxChrome {
  const n = (v: string) => parseFloat(v) || 0
  return {
    paddingLeft: n(cs.paddingLeft),
    paddingRight: n(cs.paddingRight),
    borderLeft: n(cs.borderLeftWidth),
    borderRight: n(cs.borderRightWidth),
  }
}

/**
 * The phone's second toolbar row, as styles.css lays it out (the `.mobile
 * .view-row` block): everything that isn't a select has a fixed width.
 * Keep in step with the CSS; tests/fitSelect.test.ts checks both.
 */
export const PHONE_ROW = {
  /** 390 px phone less the toolbar's 8 px side padding */
  width: 374,
  gap: 4,
  layoutToggle: 36,
  /** − n + : two 26 px buttons and an 18 px count */
  stepper: 70,
  filters: 34,
  /** select padding (left, right: the chevron sits in it) and borders */
  select: { paddingLeft: 6, paddingRight: 15, borderLeft: 1, borderRight: 1 } as BoxChrome,
}

/** The row's width with selects showing labels this wide (text px). */
export function phoneRowWidth(labelWidths: number[], r = PHONE_ROW): number {
  const fixed = r.layoutToggle + r.stepper + r.filters
  const selects = labelWidths.reduce((s, w) => s + fitSelectWidth(w, r.select), 0)
  const items = 3 + labelWidths.length // toggle, stepper, filters + selects
  return fixed + selects + r.gap * (items - 1)
}
