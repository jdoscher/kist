import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { TILE_SIZE_MAX, TILE_SIZE_MIN } from '../layout'
import { useTags } from '../data'
import { useAppState } from '../state'
import type { MobileCols } from '../mobile'
import { PasteButton } from './PasteButton'
import { useUploads } from './UploadManager'

const TYPES = ['image', 'video', 'pdf', 'other'] as const
const ORDERS: [string, string][] = [
  ['-created', 'Newest'],
  ['created', 'Oldest'],
  ['filename', 'Name'],
  ['-size', 'Largest'],
  ['-rating', 'Top rated'],
  ['-modified', 'Recently edited'],
]

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
  } = useAppState()
  const { data: tags } = useTags()
  const uploads = useUploads()
  const [text, setText] = useState(query.q)
  const [open, setOpen] = useState(false)
  const [hilite, setHilite] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  const optId = (i: number) => `${listId}-opt-${i}`

  useEffect(() => setText(query.q), [query.q])

  // every active filter shows as a chip, so nothing narrows the grid invisibly
  const hasChips =
    query.tags.length > 0 ||
    query.tags_any.length > 0 ||
    query.untagged ||
    !!query.preview ||
    !!query.created_after ||
    !!query.created_before

  // debounce free-text search
  useEffect(() => {
    const t = setTimeout(() => {
      if (text !== query.q) setQuery({ q: text })
    }, 250)
    return () => clearTimeout(t)
  }, [text]) // eslint-disable-line react-hooks/exhaustive-deps

  const suggestions = useMemo(() => {
    if (!text.trim() || !tags) return []
    const needle = text.trim().toLowerCase()
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
    setQuery({ tags: [...query.tags, name], q: '' })
    setText('')
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
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
          <span aria-hidden="true">☰</span>
        </button>
      )}
      <div className="brand" title="Kist">
        <img src="/favicon.svg?v=3" alt="" className="brand-icon" />
        Kist
      </div>

      <div className="searchbox" ref={boxRef} role="search">
        {hasChips && (
          <div className="chips" role="group" aria-label="Active filters">
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
          </div>
        )}
        <input
          value={text}
          aria-label="Search library"
          placeholder={hasChips ? 'Add search…' : 'Search filenames, #tags…'}
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
      </div>

      {mobile && <div className="topbar-break" />}

      <div className="view-controls">
        <button
          className={`layout-toggle${gridLayout === 'square' ? ' on' : ''}`}
          onClick={() => setGridLayout(gridLayout === 'justified' ? 'square' : 'justified')}
          title={
            gridLayout === 'justified'
              ? 'Tiles follow each image\'s shape. Press for square tiles.'
              : 'Square tiles. Press to show each image in its true shape.'
          }
          aria-pressed={gridLayout === 'square'}
        >
          <span aria-hidden="true">{gridLayout === 'justified' ? '▭' : '▢'}</span>
          <span className={mobile ? 'sr-only' : 'btn-text'}>Square tiles</span>
        </button>
        {mobile ? (
          <div className="cols-toggle" role="group" aria-label="Columns" title="Columns">
            {([2, 3] as MobileCols[]).map((c) => (
              <button
                key={c}
                className={mobileCols === c ? 'on' : ''}
                aria-pressed={mobileCols === c}
                onClick={() => setMobileCols(c)}
                aria-label={`${c} columns`}
              >
                {c}
              </button>
            ))}
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
        value={query.type ?? ''}
        onChange={(e) => setQuery({ type: e.target.value || null })}
        title="Type"
        aria-label="Type"
      >
        <option value="">{mobile ? 'Type' : 'All types'}</option>
        {TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>

      <select
        value={query.rating}
        onChange={(e) => setQuery({ rating: Number(e.target.value) })}
        title="Minimum rating"
        aria-label="Minimum rating"
      >
        <option value={0}>{mobile ? 'Rating' : 'Any rating'}</option>
        {[1, 2, 3, 4, 5].map((r) => (
          <option key={r} value={r} aria-label={`${r} star${r > 1 ? 's' : ''} or more`}>
            {'★'.repeat(r)}+
          </option>
        ))}
      </select>

      <select value={query.order} onChange={(e) => setQuery({ order: e.target.value })} title="Sort" aria-label="Sort">
        {ORDERS.map(([v, label]) => (
          <option key={v} value={v}>
            {mobile && v === '-modified' ? 'Edited' : label}
          </option>
        ))}
      </select>

      {!mobile && (
        <>
      <PasteButton />
      <button className="primary" onClick={() => fileRef.current?.click()}>
        Upload
      </button>
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
      <button
        className={`panel-toggle ${panelOpen ? 'on' : ''}`}
        onClick={() => setPanelOpen(!panelOpen)}
        title={panelOpen ? 'Hide the info panel' : 'Show the info panel'}
        aria-pressed={panelOpen}
      >
        Info
      </button>
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
        <span aria-hidden="true">×</span>
      </button>
    </span>
  )
}
