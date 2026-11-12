import { Columns3, Command, History, ImageUp, Info, LayoutGrid, Menu, Minus, PanelsLeftRight, Plus, Rows3, Settings, Upload, X } from 'lucide-react'
import { GRID_LAYOUTS, type GridLayout } from '../layout'
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { chromeOf, fitSelectWidth } from '../fitSelect'
import { TILE_SIZE_MAX, TILE_SIZE_MIN } from '../layout'
import { useCaps, useTags } from '../data'
import { useAppState } from '../state'
import type { ItemKind } from '../api'
import { PHONE_COLS, stepCols } from '../zoom'
import { AppearanceMenu } from './Appearance'
import { Mark, Wordmark } from './Brand'
import { PasteButton } from './PasteButton'
import { useUploads } from './UploadManager'
import { buildSearch, isRanked, modeOf, searchText, tagTokens, type SearchMode } from '../search'
import { ColourPicker, hasSearchChips, SearchChips, SearchModeMenu, useImageSearch, useSearchMode } from './SearchModes'
import { useCreative } from './m3/Creative'
import { labelName } from '../cull'
import { hasM45Filters } from '../m45/filters'
import { FiltersButton, M45Chips, ViewShareMenu } from './m45/TopBarExtras'

const PLACEHOLDER: Record<SearchMode, string> = {
  auto: 'Search, or describe it…',
  words: 'Search names, notes, text…',
  meaning: 'Describe it: red car at night…',
  text: 'Words inside images and PDFs…',
  color: 'Search…',
  image: 'Narrow with words…',
}

// every kind the server has (Milestone 5 added audio, 3D models, design
// files and bundles)
export const TYPES: [ItemKind, string][] = [
  ['image', 'Images'],
  ['video', 'Videos'],
  ['pdf', 'PDFs'],
  ['audio', 'Audio'],
  ['model', '3D models'],
  ['font', 'Fonts'],
  ['design', 'Design files'],
  ['bundle', 'Bundles'],
  ['other', 'Other'],
]
const ORDERS: [string, string][] = [
  ['-created', 'Newest'],
  ['created', 'Oldest'],
  ['filename', 'Name'],
  ['-size', 'Largest'],
  ['-rating', 'Top rated'],
  ['-modified', 'Recently edited'],
  ['-taken', 'Date taken'],
]

let measureCtx: CanvasRenderingContext2D | null | undefined

/**
 * A phone toolbar select sized to the label it shows (fitSelect.ts), so
 * "Rating" isn't cut to "R…". Measured with the select's own font, again
 * once the web font has loaded (the fallback font is wider). Off on
 * desktop: the selects keep their natural width there.
 */
function useFitSelect(on: boolean, value: string) {
  const ref = useRef<HTMLSelectElement>(null)
  const [width, setWidth] = useState<number | undefined>(undefined)
  useLayoutEffect(() => {
    const el = ref.current
    if (!on || !el) {
      setWidth(undefined)
      return
    }
    let live = true
    const measure = () => {
      if (!live) return
      if (measureCtx === undefined) measureCtx = document.createElement('canvas').getContext('2d')
      if (!measureCtx) return
      const cs = getComputedStyle(el)
      measureCtx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
      const label = el.options[el.selectedIndex]?.text ?? ''
      setWidth(fitSelectWidth(measureCtx.measureText(label).width, chromeOf(cs)))
    }
    measure()
    const fonts = document.fonts
    void fonts?.ready.then(measure)
    fonts?.addEventListener?.('loadingdone', measure)
    return () => {
      live = false
      fonts?.removeEventListener?.('loadingdone', measure)
    }
  }, [on, value])
  return { ref, style: width ? { width } : undefined }
}

/** The tile-shape control's three choices (Rows is the default). */
const LAYOUT_CHOICES: Record<GridLayout, { name: string; hint: string }> = {
  justified: { name: 'Rows', hint: "Rows: tiles follow each image's shape, rows fill the width." },
  square: { name: 'Square tiles', hint: 'Square tiles: every image cropped to a square.' },
  columns: { name: 'Columns', hint: 'Columns: fixed-width columns, every image uncropped. Size sets the column width.' },
}

const nextLayout = (l: GridLayout): GridLayout => GRID_LAYOUTS[(GRID_LAYOUTS.indexOf(l) + 1) % GRID_LAYOUTS.length]

function LayoutIcon({ layout }: { layout: GridLayout }) {
  const Icon = layout === 'square' ? LayoutGrid : layout === 'columns' ? Columns3 : Rows3
  return <Icon size={16} aria-hidden="true" />
}

export function TopBar({ mobile = false }: { mobile?: boolean }) {
  const {
    query,
    setQuery,
    panelOpen,
    setPanelOpen,
    gridLayout,
    setGridLayout,
    tileSize,
    setTileSize,
    openDrawer,
    drawerOpen,
    mobileCols,
    setMobileCols,
    density,
    chromeShown,
    setChromeShown,
    setRecentOpen,
    openSettings,
  } = useAppState()
  const caps = useCaps()
  const { data: tags } = useTags()
  const uploads = useUploads()
  const creative = useCreative()
  const [text, setText] = useState(searchText(query))
  // search modes (search.ts): the preferred one, the colour picker, images
  const [prefMode, setPrefMode] = useSearchMode()
  const mode = modeOf(query, prefMode)
  const [colourOpen, setColourOpen] = useState(false)
  const img = useImageSearch((image) => setQuery({ image, q: '', semantic: '', text: '', similar: null, color: null }))
  const ranked = isRanked(query)
  // phone: each select as wide as the option it shows (fitSelect.ts)
  const typeFit = useFitSelect(mobile, query.type ?? '')
  const ratingFit = useFitSelect(mobile, String(query.rating))
  const sortFit = useFitSelect(mobile, ranked ? '__best' : query.order)
  const [open, setOpen] = useState(false)
  const [hilite, setHilite] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  const optId = (i: number) => `${listId}-opt-${i}`

  useEffect(() => setText(searchText(query)), [query.q, query.semantic, query.text]) // eslint-disable-line react-hooks/exhaustive-deps

  // every active filter shows as a chip, so nothing narrows the grid invisibly
  const hasChips =
    hasSearchChips(query) ||
    query.tags.length > 0 ||
    query.tags_any.length > 0 ||
    query.untagged ||
    !!query.preview ||
    !!query.fixity ||
    !!query.created_after ||
    !!query.created_before ||
    !!query.flag ||
    !!query.label ||
    hasM45Filters(query)

  // debounce free-text search; what it sends depends on the mode
  useEffect(() => {
    const t = setTimeout(() => {
      if (text === searchText(query)) return
      // words typed over an image search narrow it
      // #tags become tag chips on Enter; until then they don't search as words
      const words = tagTokens(text).rest
      if (words === searchText(query)) return
      if (query.image) setQuery({ q: words })
      else setQuery(buildSearch(words, prefMode))
    }, 300)
    return () => clearTimeout(t)
  }, [text]) // eslint-disable-line react-hooks/exhaustive-deps

  const pickMode = (m: SearchMode) => {
    if (m === 'color') return setColourOpen(true)
    if (m === 'image') return img.pick()
    setPrefMode(m)
    if (text.trim()) setQuery(buildSearch(text, m))
  }

  const suggestions = useMemo(() => {
    if (!text.trim() || !tags) return []
    const needle = text.trim().replace(/^#/, '').toLowerCase()
    if (!needle) return []
    return tags
      .filter((t) => t.name.toLowerCase().includes(needle) && !query.tags.includes(t.name))
      .slice(0, 8)
  }, [text, tags, query.tags])

  useEffect(() => setHilite(0), [suggestions.length])

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [])

  const addTag = (name: string) => {
    setQuery({ tags: [...query.tags, name], q: '', semantic: '', text: '' })
    setText('')
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Enter on "#tag" words (implied tags and "#brand:*" too, which the
    // suggestions don't list) adds them as tag chips
    const typed = tagTokens(text)
    if (e.key === 'Enter' && typed.tags.length && !(open && suggestions[hilite])) {
      e.preventDefault()
      const add = typed.tags.filter((t) => !query.tags.includes(t))
      setQuery({ ...buildSearch(typed.rest, prefMode), tags: [...query.tags, ...add] })
      setText(typed.rest)
      setOpen(false)
      return
    }
    if (!open || suggestions.length === 0) {
      if (e.key === 'Backspace' && !text && query.tags.length) {
        setQuery({ tags: query.tags.slice(0, -1) })
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHilite((h) => Math.min(h + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHilite((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter' && suggestions[hilite]) {
      e.preventDefault()
      addTag(suggestions[hilite].name)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <header className="topbar">
      <h1 className="sr-only">Kist library</h1>
      {mobile && (
        <button
          className="menu-btn"
          onClick={openDrawer}
          aria-label="Folders and tags"
          aria-expanded={drawerOpen}
          aria-controls="folder-drawer"
        >
          <Menu size={20} aria-hidden="true" />
        </button>
      )}
      <div className="brand" title="Kist">
        <Mark />
        <Wordmark />
      </div>

      <div
        className={`searchbox${img.over ? ' drop-target' : ''}`}
        ref={boxRef}
        role="search"
        {...img.handlers}
      >
        <SearchModeMenu mode={mode} onPick={pickMode} compact={mobile} />
        {hasChips && (
          <div className="chips" role="group" aria-label="Active filters">
            <SearchChips query={query} setQuery={setQuery} />
            {query.tags.map((t) => (
              <Chip key={t} label={`#${t}`} clear={`Remove tag ${t}`} onClear={() => setQuery({ tags: query.tags.filter((x) => x !== t) })} />
            ))}
            {query.tags_any.length > 0 && (
              <Chip
                label={`any of ${query.tags_any.map((t) => `#${t}`).join(' ')}`}
                title="Items with at least one of these tags"
                clear="Clear any-of tags"
                onClear={() => setQuery({ tags_any: [] })}
              />
            )}
            {query.preview && (
              <Chip label={`preview ${query.preview}`} clear="Clear preview filter" onClear={() => setQuery({ preview: null })} />
            )}
            {query.fixity && (
              <Chip label="fixity check failed" clear="Clear fixity filter" onClear={() => setQuery({ fixity: null })} />
            )}
            {query.flag && (
              <Chip label={query.flag === 'pick' ? 'picks' : query.flag === 'reject' ? 'rejects' : 'unflagged'} clear="Clear flag filter" onClear={() => setQuery({ flag: null })} />
            )}
            {query.label && (
              <Chip label={query.label === 'none' ? 'no label' : `${labelName(query.label).toLowerCase()} label`} clear="Clear label filter" onClear={() => setQuery({ label: null })} />
            )}
            {query.untagged && (
              <Chip label="untagged" clear="Clear untagged filter" onClear={() => setQuery({ untagged: false })} />
            )}
            {query.created_after && (
              <Chip
                label={`added after ${query.created_after.slice(0, 10)}`}
                clear="Clear added-after date"
                onClear={() => setQuery({ created_after: null })}
              />
            )}
            {query.created_before && (
              <Chip
                label={`added before ${query.created_before.slice(0, 10)}`}
                clear="Clear added-before date"
                onClear={() => setQuery({ created_before: null })}
              />
            )}
            <M45Chips query={query} setQuery={setQuery} />
          </div>
        )}
        <input
          value={text}
          aria-label="Search library"
          placeholder={img.over ? 'Drop to search by this image' : hasChips ? 'Add search…' : PLACEHOLDER[mode]}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open && suggestions.length > 0}
          aria-controls={listId}
          aria-activedescendant={open && suggestions.length > 0 ? optId(hilite) : undefined}
          onChange={(e) => {
            setText(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <div id={listId} className="autocomplete" role="listbox" aria-label="Tag suggestions" hidden={!open || suggestions.length === 0}>
          {open &&
            suggestions.map((s, i) => (
              <div
                key={s.id}
                id={optId(i)}
                role="option"
                aria-selected={i === hilite}
                className={`ac-option${i === hilite ? ' active' : ''}`}
                onMouseEnter={() => setHilite(i)}
                onMouseDown={(e) => e.preventDefault() /* keep focus in the field */}
                onClick={() => addTag(s.name)}
              >
                #{s.name} <span className="count">{s.count}</span>
              </div>
            ))}
        </div>
        <span className="sr-only" role="status">
          {open && suggestions.length > 0 ? `${suggestions.length} tag suggestions, use up and down to pick` : ''}
        </span>
        <button
          type="button"
          className="search-image-btn"
          onClick={img.pick}
          aria-label="Search by image"
          title="Search by image: choose a picture, or drop or paste one onto the search box (it isn’t saved)"
        >
          <ImageUp size={16} aria-hidden="true" />
        </button>
        {img.input}
        {colourOpen && (
          <ColourPicker
            initial={query.color}
            tol={query.color_tol}
            onClose={() => setColourOpen(false)}
            onSearch={(color, color_tol) => {
              setColourOpen(false)
              setQuery({ color, color_tol, q: '', semantic: '', text: '', similar: null, image: null })
            }}
          />
        )}
      </div>

      <button className="icon-btn topbar-icon palette-btn" onClick={creative.openPalette} aria-label="Go to or do anything (⌘K)" title="Go to or do anything (⌘K)" aria-keyshortcuts="Meta+K Control+K">
        <Command size={16} aria-hidden="true" />
      </button>
      {/* a phone's second row is full: share/export sits beside the palette */}
      {mobile && <ViewShareMenu />}

      {mobile && <div className="topbar-break" />}

      {/* one row on a phone (no wrapping; display: contents on desktop) */}
      <div className="view-row">
      <div className="view-controls">
        {mobile ? (
          // the phone row has room for one button: it steps Rows -> Square -> Columns
          <button
            className={`layout-toggle${gridLayout !== 'justified' ? ' on' : ''}`}
            onClick={() => setGridLayout(nextLayout(gridLayout))}
            aria-label={`Tile shape: ${LAYOUT_CHOICES[gridLayout].name}. Press for ${LAYOUT_CHOICES[nextLayout(gridLayout)].name.toLowerCase()}`}
            title={`${LAYOUT_CHOICES[gridLayout].hint} Press for ${LAYOUT_CHOICES[nextLayout(gridLayout)].name.toLowerCase()}.`}
          >
            <LayoutIcon layout={gridLayout} />
          </button>
        ) : (
          <div className="layout-choice" role="group" aria-label="Tile shape">
            {GRID_LAYOUTS.map((l) => (
              <button
                key={l}
                className={`layout-toggle${gridLayout === l ? ' on' : ''}`}
                onClick={() => setGridLayout(l)}
                aria-pressed={gridLayout === l}
                aria-label={LAYOUT_CHOICES[l].name}
                title={LAYOUT_CHOICES[l].hint}
              >
                <LayoutIcon layout={l} />
              </button>
            ))}
          </div>
        )}
        {mobile ? (
          // pinch is the quick way; these step through the same columns
          // (1 = the feed, 2, 3, 4, 6) for one hand, keyboards and screen readers
          <div className="cols-toggle cols-stepper" role="group" aria-label="Columns">
            <button
              onClick={() => setMobileCols(stepCols(mobileCols, -1))}
              disabled={mobileCols === PHONE_COLS[0]}
              aria-label="Fewer columns"
              title="Fewer columns (or pinch out on the grid)"
            >
              <Minus size={14} aria-hidden="true" />
            </button>
            <span className="cols-now" aria-live="polite">
              {mobileCols}
              <span className="sr-only">{mobileCols === 1 ? ' column (feed)' : ' columns'}</span>
            </span>
            <button
              onClick={() => setMobileCols(stepCols(mobileCols, 1))}
              disabled={mobileCols === PHONE_COLS[PHONE_COLS.length - 1]}
              aria-label="More columns"
              title="More columns (or pinch in on the grid)"
            >
              <Plus size={14} aria-hidden="true" />
            </button>
          </div>
        ) : (
        <label className="size-label">
          <span>Size</span>
          <input
            type="range"
            className="size-slider"
            min={TILE_SIZE_MIN}
            max={TILE_SIZE_MAX}
            step={10}
            value={tileSize}
            onChange={(e) => setTileSize(Number(e.target.value))}
            onDoubleClick={() => setTileSize(200)}
            title={`Tile size: ${tileSize} px (double-click to reset)`}
            aria-label="Tile size"
            aria-valuetext={`${tileSize} pixels`}
          />
        </label>
        )}
      </div>

      <select
        {...typeFit}
        value={query.type ?? ''}
        onChange={(e) => setQuery({ type: e.target.value || null })}
        title="Type"
        aria-label="Type"
      >
        <option value="">{mobile ? 'Type' : 'All types'}</option>
        {TYPES.map(([t, label]) => (
          <option key={t} value={t}>
            {label}
          </option>
        ))}
      </select>

      <FiltersButton mobile={mobile} />

      <select
        {...ratingFit}
        value={query.rating}
        onChange={(e) => setQuery({ rating: Number(e.target.value) })}
        title="Minimum rating"
        aria-label="Minimum rating"
      >
        <option value={0}>{mobile ? 'Rating' : 'Any rating'}</option>
        {[1, 2, 3, 4, 5].map((r) => (
          <option key={r} value={r}>
            {r}+ star{r > 1 ? 's' : ''}
          </option>
        ))}
      </select>

      <select
        {...sortFit}
        value={ranked ? '__best' : query.order}
        onChange={(e) => setQuery({ order: e.target.value })}
        title={ranked ? 'Ranked by best match while searching by meaning, colour, text or image' : 'Sort'}
        aria-label="Sort"
        disabled={ranked}
      >
        {ranked && <option value="__best">Best match</option>}
        {ORDERS.map(([v, label]) => (
          <option key={v} value={v}>
            {mobile && v === '-modified' ? 'Edited' : label}
          </option>
        ))}
      </select>
      </div>

      {!mobile && (
        <>
      {caps.canEdit && (
        <>
          <PasteButton />
          <button className="btn-primary btn-icon" onClick={() => fileRef.current?.click()}>
            <Upload size={16} aria-hidden="true" />
            Upload
          </button>
        </>
      )}
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        aria-hidden="true"
        tabIndex={-1}
        onChange={(e) => {
          if (e.target.files?.length) uploads.enqueue(Array.from(e.target.files))
          e.target.value = ''
        }}
      />
      {density === 'gallery' ? (
        <button
          className={`panel-toggle btn-icon ${chromeShown ? 'on' : ''}`}
          onClick={() => setChromeShown(!chromeShown)}
          title={chromeShown ? 'Hide the sidebar and info panel' : 'Show the sidebar and info panel'}
          aria-pressed={chromeShown}
        >
          <PanelsLeftRight size={16} aria-hidden="true" />
          Panels
        </button>
      ) : (
        <button
          className={`panel-toggle btn-icon ${panelOpen ? 'on' : ''}`}
          onClick={() => setPanelOpen(!panelOpen)}
          title={panelOpen ? 'Hide the info panel' : 'Show the info panel'}
          aria-pressed={panelOpen}
        >
          <Info size={16} aria-hidden="true" />
          Info
        </button>
      )}
      {caps.canEdit && (
        <button
          className="icon-btn topbar-icon"
          onClick={() => setRecentOpen(true)}
          aria-label="Recent changes"
          title="Recent changes and undo (⌘Z / Ctrl+Z)"
          aria-keyshortcuts="Meta+Z Control+Z"
        >
          <History size={16} aria-hidden="true" />
        </button>
      )}
      <ViewShareMenu />
      <AppearanceMenu />
      {caps.isAdmin && (
        <button className="icon-btn topbar-icon" onClick={() => openSettings()} aria-label="Settings" title="Settings: tokens, archive health, AI">
          <Settings size={16} aria-hidden="true" />
        </button>
      )}
        </>
      )}
    </header>
  )
}

/** A filter chip with its remove button (named for what it removes). */
function Chip({ label, clear, onClear, title }: { label: string; clear: string; onClear: () => void; title?: string }) {
  return (
    <span className="chip" title={title}>
      {label}
      <button aria-label={clear} title={clear} onClick={onClear}>
        <X size={14} aria-hidden="true" />
      </button>
    </span>
  )
}
