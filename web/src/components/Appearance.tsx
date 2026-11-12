// Appearance: theme (System / Light / Dark / Gallery grey) and density
// (Compact / Comfortable / Gallery). A popover from the top bar on
// desktop; inline in the drawer on phones. Choices persist (settings.ts)
// and apply at once (theme.ts).

import { Palette } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useAppState } from '../state'
import { DENSITIES, THEME_PREFS, type Density, type ThemePref } from '../theme'

// each theme's image canvas, as a colour chip (System: half light, half
// dark). Literal values: a CSS variable would give the current theme's.
const SWATCH: Record<ThemePref, string> = {
  system: 'linear-gradient(135deg, #ececec 50%, #131312 50%)',
  light: '#ececec',
  dark: '#131312',
  gallery: '#5c5c5c',
}

/** The two radio groups. */
export function AppearanceControls() {
  const { theme, setTheme, density, setDensity, mobile } = useAppState()
  const id = useId()
  return (
    <div className="appearance">
      <fieldset>
        <legend>Theme</legend>
        {THEME_PREFS.map((t) => (
          <label key={t.value}>
            <input
              type="radio"
              name={`${id}-theme`}
              value={t.value}
              checked={theme === t.value}
              onChange={() => setTheme(t.value)}
            />
            <span>
              {t.label}
              {t.hint && <span className="opt-hint">{t.hint}</span>}
            </span>
            <span className="swatch" aria-hidden="true" style={{ background: SWATCH[t.value] }} />
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Density</legend>
        {DENSITIES.filter((d) => !(mobile && d.value === 'gallery')).map((d) => (
          <label key={d.value}>
            <input
              type="radio"
              name={`${id}-density`}
              value={d.value}
              checked={density === d.value}
              onChange={() => setDensity(d.value as Density)}
            />
            <span>
              {d.label}
              {d.hint && !mobile && <span className="opt-hint">{d.hint}</span>}
            </span>
          </label>
        ))}
      </fieldset>
    </div>
  )
}

/** Top-bar button + popover. Escape or a click outside closes it. */
export function AppearanceMenu() {
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const id = useId()

  useEffect(() => {
    if (!open) return
    popRef.current?.querySelector<HTMLInputElement>('input:checked')?.focus()
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  return (
    <div
      className="appearance-wrap"
      ref={boxRef}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.stopPropagation()
          setOpen(false)
          btnRef.current?.focus()
        }
      }}
    >
      <button
        ref={btnRef}
        className="icon-btn"
        onClick={() => setOpen(!open)}
        title="Appearance: theme and density"
        aria-label="Appearance"
        aria-expanded={open}
        aria-controls={`${id}-pop`}
      >
        <Palette size={16} aria-hidden="true" />
      </button>
      <div id={`${id}-pop`} ref={popRef} className="appearance-pop" role="group" aria-label="Appearance" hidden={!open}>
        {open && <AppearanceControls />}
      </div>
    </div>
  )
}
