import { useEffect, useMemo, useRef, useState } from 'react'
import { useAppState, useTags } from '../state'
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

export function TopBar() {
  const { query, setQuery, panelOpen, setPanelOpen } = useAppState()
  const { data: tags } = useTags()
  const uploads = useUploads()
  const [text, setText] = useState(query.q)
  const [open, setOpen] = useState(false)
  const [hilite, setHilite] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => setText(query.q), [query.q])

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
      <div className="brand" title="Kist">
        ⟡ Kist
      </div>

      <div className="searchbox" ref={boxRef}>
        {query.tags.map((t) => (
          <span key={t} className="chip">
            #{t}
            <button onClick={() => setQuery({ tags: query.tags.filter((x) => x !== t) })}>×</button>
          </span>
        ))}
        <input
          value={text}
          placeholder={query.tags.length ? 'Add search…' : 'Search filenames, #tags…'}
          onChange={(e) => {
            setText(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        {open && suggestions.length > 0 && (
          <div className="autocomplete">
            {suggestions.map((s, i) => (
              <button
                key={s.id}
                className={i === hilite ? 'active' : ''}
                onMouseEnter={() => setHilite(i)}
                onClick={() => addTag(s.name)}
              >
                #{s.name} <span className="count">{s.count}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <select
        value={query.type ?? ''}
        onChange={(e) => setQuery({ type: e.target.value || null })}
        title="Type"
      >
        <option value="">All types</option>
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
      >
        <option value={0}>Any rating</option>
        {[1, 2, 3, 4, 5].map((r) => (
          <option key={r} value={r}>
            {'★'.repeat(r)}+
          </option>
        ))}
      </select>

      <select value={query.order} onChange={(e) => setQuery({ order: e.target.value })} title="Sort">
        {ORDERS.map(([v, label]) => (
          <option key={v} value={v}>
            {label}
          </option>
        ))}
      </select>

      <PasteButton />
      <button className="primary" onClick={() => fileRef.current?.click()}>
        Upload
      </button>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
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
    </header>
  )
}
