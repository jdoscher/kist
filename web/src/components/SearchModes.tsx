// The search box's extras (TopBar renders them): the mode menu, the colour
// picker, search by image (button, drop or paste onto the box) and the
// chips for searches that started from an item (more like this, a colour,
// an image, a person). Rules in search.ts.

import { useQuery } from '@tanstack/react-query'
import { Check, ChevronDown, ImageUp, Palette, ScanText, Sparkles, Type, Wand2, X, type LucideIcon } from 'lucide-react'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { api, type Query } from '../api'
import { firstImage, imagePreviewUrl, registerImage } from '../imageSearch'
import { org } from '../organiseApi'
import { DEFAULT_COLOR_TOL, hexColor, SEARCH_MODES, type SearchMode } from '../search'
import { isBookRef } from '../colourBooks'
import { CodeChipText, CodeSearch } from './colourbooks/ColourBooks'

const MODE_KEY = 'kist.searchMode'

const MODE_ICON: Record<SearchMode, LucideIcon> = {
  auto: Wand2,
  words: Type,
  meaning: Sparkles,
  text: ScanText,
  color: Palette,
  image: ImageUp,
}

function readMode(): SearchMode {
  try {
    const v = localStorage.getItem(MODE_KEY)
    return SEARCH_MODES.some((m) => m.id === v) ? (v as SearchMode) : 'auto'
  } catch {
    return 'auto'
  }
}

/** The preferred mode (kept per browser; Auto by default). Colour and
 * Image are one-off actions, so they're never remembered. */
export function useSearchMode(): [SearchMode, (m: SearchMode) => void] {
  const [mode, setMode] = useState<SearchMode>(readMode)
  const set = useCallback((m: SearchMode) => {
    setMode(m)
    if (m === 'color' || m === 'image') return
    try {
      localStorage.setItem(MODE_KEY, m)
    } catch {
      /* private mode */
    }
  }, [])
  return [mode, set]
}

/** The button left of the search field: shows the mode, opens the menu. */
export function SearchModeMenu({
  mode,
  onPick,
  compact = false,
}: {
  mode: SearchMode
  onPick: (m: SearchMode) => void
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const id = useId()
  const wrap = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const current = SEARCH_MODES.find((m) => m.id === mode) ?? SEARCH_MODES[0]
  const Icon = MODE_ICON[current.id]

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])

  useEffect(() => {
    if (open) {
      const i = Math.max(0, SEARCH_MODES.findIndex((m) => m.id === mode))
      setHi(i)
      requestAnimationFrame(() => document.getElementById(`${id}-m${i}`)?.focus())
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (m: SearchMode) => {
    setOpen(false)
    btn.current?.focus()
    onPick(m)
  }

  const onMenuKey = (e: React.KeyboardEvent) => {
    let next = hi
    if (e.key === 'ArrowDown') next = (hi + 1) % SEARCH_MODES.length
    else if (e.key === 'ArrowUp') next = (hi - 1 + SEARCH_MODES.length) % SEARCH_MODES.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = SEARCH_MODES.length - 1
    else if (e.key === 'Escape' || e.key === 'Tab') {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        btn.current?.focus()
      }
      setOpen(false)
      return
    } else return
    e.preventDefault()
    setHi(next)
    document.getElementById(`${id}-m${next}`)?.focus()
  }

  return (
    <div className="search-mode" ref={wrap}>
      <button
        ref={btn}
        type="button"
        className="search-mode-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? `${id}-menu` : undefined}
        aria-label={`Search mode: ${current.label}`}
        title={`Search mode: ${current.label}. ${current.hint}`}
        onClick={() => setOpen((o) => !o)}
      >
        <Icon size={16} aria-hidden="true" />
        {!compact && <span className="search-mode-label">{current.label}</span>}
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div id={`${id}-menu`} className="search-mode-menu" role="menu" aria-label="Search mode" onKeyDown={onMenuKey}>
          {SEARCH_MODES.map((m, i) => {
            const MIcon = MODE_ICON[m.id]
            const checked = m.id === mode
            return (
              <button
                key={m.id}
                id={`${id}-m${i}`}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                tabIndex={i === hi ? 0 : -1}
                className={`search-mode-item${checked ? ' on' : ''}`}
                onClick={() => pick(m.id)}
              >
                <MIcon size={16} aria-hidden="true" />
                <span className="smi-text">
                  <span className="smi-label">{m.label}</span>
                  <span className="smi-hint">{m.hint}</span>
                </span>
                {checked && <Check size={14} aria-hidden="true" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

const SWATCHES = ['#c0392b', '#e67e22', '#f1c40f', '#2e7d32', '#2f4f3a', '#1e88e5', '#283593', '#8e24aa', '#6d4c41', '#d7ccc8', '#212121', '#f5f5f5']

/** The colour search popover: a picker, a hex field, a tolerance slider. */
export function ColourPicker({
  initial,
  tol,
  onSearch,
  onClose,
}: {
  initial: string | null
  tol: number
  onSearch: (hex: string, tol: number) => void
  onClose: () => void
}) {
  const id = useId()
  // a colour book code (F5) isn't a hex: start from the default
  const [hex, setHex] = useState(hexColor(initial ?? '', false) ?? '#2f4f3a')
  const [text, setText] = useState(hexColor(initial ?? '', false) ?? '#2f4f3a')
  const [t, setT] = useState(tol || DEFAULT_COLOR_TOL)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    window.addEventListener('mousedown', close)
    requestAnimationFrame(() => ref.current?.querySelector<HTMLInputElement>('input[type=color]')?.focus())
    return () => window.removeEventListener('mousedown', close)
  }, [onClose])
  const valid = hexColor(text, false)
  return (
    <div
      ref={ref}
      className="colour-pop"
      role="dialog"
      aria-labelledby={`${id}-t`}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation()
          onClose()
        }
      }}
    >
      <div id={`${id}-t`} className="colour-pop-title">
        Search by colour
      </div>
      <div className="colour-pop-row">
        <input
          type="color"
          aria-label="Colour"
          value={hex}
          onChange={(e) => {
            setHex(e.target.value)
            setText(e.target.value)
          }}
        />
        <label className="sr-only" htmlFor={`${id}-hex`}>
          Hex colour
        </label>
        <input
          id={`${id}-hex`}
          className="colour-hex"
          value={text}
          spellCheck={false}
          aria-invalid={!valid}
          onChange={(e) => {
            setText(e.target.value)
            const h = hexColor(e.target.value, false)
            if (h) setHex(h)
          }}
          onKeyDown={(e) => e.key === 'Enter' && valid && onSearch(valid, t)}
        />
      </div>
      <div className="colour-swatches" role="group" aria-label="Common colours">
        {SWATCHES.map((s) => (
          <button
            key={s}
            type="button"
            className={`swatch-btn${s === hex ? ' on' : ''}`}
            style={{ background: s }}
            aria-label={s}
            aria-pressed={s === hex}
            title={s}
            onClick={() => {
              setHex(s)
              setText(s)
            }}
          />
        ))}
      </div>
      {/* F5: or a code from the user's colour books */}
      <CodeSearch onPick={(ref) => onSearch(ref, t)} />
      <label className="colour-tol">
        <span>
          How close: <strong>{t <= 15 ? 'exact' : t <= 40 ? 'near' : 'loose'}</strong>
        </span>
        <input type="range" min={0} max={100} step={5} value={t} onChange={(e) => setT(Number(e.target.value))} aria-valuetext={`${t} of 100`} />
      </label>
      <div className="colour-pop-actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="btn-primary" disabled={!valid} onClick={() => valid && onSearch(valid, t)}>
          Search
        </button>
      </div>
    </div>
  )
}

/** Drop or paste an image onto the search box, or pick one: returns the
 * handlers and the hidden file input. `onImage` gets the registered id. */
export function useImageSearch(onImage: (id: string) => void) {
  const [over, setOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const take = (f: File | null) => {
    if (!f) return false
    onImage(registerImage(f))
    return true
  }
  const handlers = {
    onDragOver: (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes('Files')) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'copy'
      setOver(true)
    },
    onDragLeave: () => setOver(false),
    onDrop: (e: React.DragEvent) => {
      setOver(false)
      const f = firstImage(e.dataTransfer.files)
      if (!f) return
      // the image searches; it isn't added to the library (UploadManager
      // skips a drop that's already handled, and still clears its overlay)
      e.preventDefault()
      take(f)
    },
    onPaste: (e: React.ClipboardEvent) => {
      const f = firstImage(e.clipboardData?.files)
      if (f) {
        e.preventDefault()
        take(f)
      }
    },
  }
  const input = (
    <input
      ref={fileRef}
      type="file"
      accept="image/*"
      hidden
      aria-hidden="true"
      tabIndex={-1}
      onChange={(e) => {
        take(firstImage(e.target.files))
        e.target.value = ''
      }}
    />
  )
  return { over, handlers, input, pick: () => fileRef.current?.click() }
}

function ImageChipThumb({ id }: { id: string }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    const u = imagePreviewUrl(id)
    setUrl(u)
    return () => {
      if (u) URL.revokeObjectURL(u)
    }
  }, [id])
  return url ? <img className="chip-thumb" src={url} alt="" /> : null
}

/** Chips for the searches that came from an item, a colour or an image. */
export function SearchChips({ query, setQuery }: { query: Query; setQuery: (p: Partial<Query>) => void }) {
  const similar = useQuery({
    queryKey: ['item', query.similar],
    queryFn: () => api.getItem(query.similar as string),
    enabled: !!query.similar,
    retry: false,
  })
  const people = useQuery({ queryKey: ['people'], queryFn: org.people, enabled: !!query.person, retry: false })
  const person = query.person ? people.data?.find((p) => p.id === query.person) : undefined
  return (
    <>
      {query.similar && (
        <span className="chip search-chip" title="Items that look like this one, best first">
          {similar.data?.thumb_url && <img className="chip-thumb" src={`${similar.data.thumb_url}`} alt="" />}
          More like {similar.data ? `“${similar.data.filename}”` : 'this'}
          <button aria-label="Clear more like this" title="Clear more like this" onClick={() => setQuery({ similar: null })}>
            <X size={14} aria-hidden="true" />
          </button>
        </span>
      )}
      {query.color && (
        <span className="chip search-chip" title={`Items with ${query.color} in their palette`}>
          {isBookRef(query.color) ? (
            <CodeChipText value={query.color} />
          ) : (
            <>
              <span className="chip-swatch" style={{ background: query.color }} aria-hidden="true" />
              colour {query.color}
            </>
          )}
          <button aria-label="Clear colour" title="Clear colour" onClick={() => setQuery({ color: null })}>
            <X size={14} aria-hidden="true" />
          </button>
        </span>
      )}
      {query.image && (
        <span className="chip search-chip" title="Items that look like the image you dropped (it isn’t saved)">
          <ImageChipThumb id={query.image} />
          like your image
          <button aria-label="Clear image search" title="Clear image search" onClick={() => setQuery({ image: null })}>
            <X size={14} aria-hidden="true" />
          </button>
        </span>
      )}
      {query.person && (
        <span className="chip search-chip">
          {person?.cover && <img className="chip-thumb round" src={person.cover.thumb_url} alt="" />}
          {person?.name ?? 'Unnamed person'}
          <button aria-label="Clear person" title="Clear person" onClick={() => setQuery({ person: null })}>
            <X size={14} aria-hidden="true" />
          </button>
        </span>
      )}
    </>
  )
}

export function hasSearchChips(q: Query): boolean {
  return !!(q.similar || q.color || q.image || q.person)
}
