// Colour books (F5): Settings → Colour books (import, enable/disable,
// rename, delete), the nearest codes under an item's colours, the code
// search in the colour popover, and the search chip for a code.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Palette, Trash2, Upload } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { Item } from '../../api'
import { BOOK_ACCEPT, cbApi, deText, deWords, parseRef, swatchLabel, type ColourBook, type FoundSwatch } from '../../colourBooks'
import { useCaps } from '../../data'
import { useAppState } from '../../state'
import { colourSearch } from '../../search'
import { useWrite } from '../../useWrite'
import { describeError } from '../../writes'
import { useDialog } from '../Dialog'
import { useNotices } from '../Notices'

// ---------- Settings → Colour books ----------

export function ColourBooksSection() {
  const caps = useCaps()
  const qc = useQueryClient()
  const w = useWrite()
  const dialog = useDialog()
  const { notify } = useNotices()
  const uid = useId()
  const books = useQuery({ queryKey: ['colourbooks'], queryFn: cbApi.list })
  const fileRef = useRef<HTMLInputElement>(null)
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['colourbooks'] })
    void qc.invalidateQueries({ queryKey: ['palette-codes'] })
  }

  const doImport = () => {
    if (!file) return
    void w.run('cb-import', () => cbApi.import(file, name), {
      what: 'import the colour book',
      onDone: (r) => {
        refresh()
        setFile(null)
        setName('')
        if (fileRef.current) fileRef.current.value = ''
        const extra = [r.skipped ? `${r.skipped} line${r.skipped === 1 ? '' : 's'} skipped` : '', ...r.warnings].filter(Boolean).join('; ')
        notify({ message: `Imported “${r.name}”: ${r.count} colours.${extra ? ` ${extra}.` : ''}` })
      },
    })
  }
  const toggle = (b: ColourBook) =>
    void w.run(`cb:${b.id}`, () => cbApi.patch(b.id, { enabled: !b.enabled }), { what: 'change the colour book', onDone: refresh })
  const rename = async (b: ColourBook) => {
    const next = await dialog.prompt({ title: 'Rename colour book', label: 'Name', initial: b.name, confirmLabel: 'Rename' })
    if (!next || next.trim() === b.name) return
    void w.run(`cb:${b.id}`, () => cbApi.patch(b.id, { name: next.trim() }), { what: 'rename the colour book', onDone: refresh })
  }
  const remove = async (b: ColourBook) => {
    const ok = await dialog.confirm({
      title: `Delete “${b.name}”?`,
      message: 'Its codes stop showing next to colours, and searches by its codes stop working. Import the file again to bring it back.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    void w.run(`cb:${b.id}`, () => cbApi.remove(b.id), {
      what: 'delete the colour book',
      onDone: () => {
        refresh()
        notify({ message: `Deleted “${b.name}”.` })
      },
    })
  }

  return (
    <section aria-labelledby={`${uid}-h`}>
      <div className="settings-section-head">
        <h2 id={`${uid}-h`} className="view-title">
          Colour books
        </h2>
      </div>
      <p className="settings-intro">
        Load colour lists you have the right to use (house colours, RAL, TCX, NCS) and Kist shows the nearest code under each item’s colours, with how
        far off it is (ΔE, CIEDE2000). Search by a code from the colour search. Kist ships no colour books. Files: Adobe Swatch Exchange (.ase), GIMP
        palette (.gpl) or CSV with columns <span className="mono">code, name</span> and <span className="mono">hex</span> or{' '}
        <span className="mono">L, a, b</span> (Lab D65; add an <span className="mono">illuminant</span> column for D50).
      </p>
      {caps.canEdit && (
        <div className="cbook-import">
          <input
            ref={fileRef}
            type="file"
            accept={BOOK_ACCEPT}
            aria-label="Colour book file"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <label className="sr-only" htmlFor={`${uid}-name`}>
            Name (optional)
          </label>
          <input id={`${uid}-name`} placeholder="Name (optional)" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
          <button type="button" className="btn-primary btn-icon" disabled={!file || w.busy('cb-import')} onClick={doImport}>
            <Upload size={14} aria-hidden="true" /> Import
          </button>
        </div>
      )}
      {books.isLoading && <p className="hint" role="status">Loading…</p>}
      {books.isError && <p role="alert">Couldn’t load the colour books: {describeError(books.error)}</p>}
      {books.data && books.data.length === 0 && <p className="hint">No colour books yet.</p>}
      <ul className="cbook-list">
        {(books.data ?? []).map((b) => (
          <BookRow key={b.id} book={b} canEdit={caps.canEdit} canDelete={caps.canDestroy} onToggle={toggle} onRename={rename} onDelete={remove} />
        ))}
      </ul>
    </section>
  )
}

function BookRow({
  book,
  canEdit,
  canDelete,
  onToggle,
  onRename,
  onDelete,
}: {
  book: ColourBook
  canEdit: boolean
  canDelete: boolean
  onToggle: (b: ColourBook) => void
  onRename: (b: ColourBook) => void
  onDelete: (b: ColourBook) => void
}) {
  // a strip of up to 48 of its colours, evenly picked
  const full = useQuery({ queryKey: ['colourbooks', book.id], queryFn: () => cbApi.get(book.id), staleTime: 60_000 })
  const sw = full.data?.swatches ?? []
  const step = Math.max(1, Math.ceil(sw.length / 48))
  const strip = sw.filter((_, i) => i % step === 0).slice(0, 48)
  return (
    <li className={`cbook-row${book.enabled ? '' : ' cbook-off'}`}>
      <div>
        <div className="cbook-name">{book.name}</div>
        <div className="cbook-meta">
          {book.count} colours · {book.format.toUpperCase()}
          {book.source_filename ? ` · ${book.source_filename}` : ''}
          {book.enabled ? '' : ' · off'}
        </div>
      </div>
      <div className="cbook-actions">
        {canEdit && (
          <label className="check-inline">
            <input type="checkbox" checked={book.enabled} onChange={() => onToggle(book)} /> Use
          </label>
        )}
        {canEdit && (
          <button type="button" onClick={() => onRename(book)}>
            Rename
          </button>
        )}
        {canDelete && (
          <button type="button" className="icon-btn danger" aria-label={`Delete ${book.name}`} title="Delete" onClick={() => onDelete(book)}>
            <Trash2 size={14} aria-hidden="true" />
          </button>
        )}
      </div>
      {strip.length > 0 && (
        <div className="cbook-strip" aria-hidden="true">
          {strip.map((s, i) => (
            <span key={i} style={{ background: s.hex }} title={swatchLabel(s)} />
          ))}
        </div>
      )}
    </li>
  )
}

// ---------- under an item's colours ----------

/** The nearest code in each enabled book for each palette colour; nothing
 * when there are no books. Press one to find items in that code's colour. */
export function PaletteCodes({ item }: { item: Pick<Item, 'id' | 'preview_rev' | 'dominant_colors'> }) {
  const { query, setQuery } = useAppState()
  const { data } = useQuery({
    queryKey: ['palette-codes', item.id, item.preview_rev],
    queryFn: () => cbApi.paletteCodes(item.id),
    enabled: !!item.dominant_colors?.length,
    staleTime: 60_000,
    retry: false,
  })
  const rows = (data ?? []).filter((p) => p.matches.length)
  if (!rows.length) return null
  return (
    <ul className="cbook-codes" aria-label="Nearest codes from your colour books">
      {rows.map((p) => {
        const m = p.matches[0]
        const others = p.matches.slice(1).map((o) => `${o.book}: ${swatchLabel(o)} (${deText(o.de)})`)
        return (
          <li key={p.hex} className="cbook-code">
            <span className="cbook-chip" style={{ background: p.hex }} title={`This colour, ${p.hex}`} aria-hidden="true" />
            <span className="cbook-chip" style={{ background: m.hex }} title={`${m.code}, ${m.hex}`} aria-hidden="true" />
            <button
              type="button"
              className="cbook-code-btn"
              title={`${m.book}: ${swatchLabel(m)} ${m.hex}${m.approx ? ' (approximate)' : ''} — ${deWords(m.de)}${others.length ? `\nAlso: ${others.join('; ')}` : ''}\nPress to find items in this colour`}
              aria-label={`${p.hex} is nearest to ${swatchLabel(m)} in ${m.book}, ${deText(m.de)}, ${deWords(m.de)}. Find items in this colour`}
              onClick={() => setQuery(colourSearch(`book:${m.book_id}/${m.code}`, query))}
            >
              {m.code} {m.name && <span className="cbook-code-name">{m.name}</span>}
            </button>
            <span className="cbook-de" title={`${deWords(m.de)} (CIEDE2000)`}>
              {m.approx ? '≈ ' : ''}
              {deText(m.de)}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// ---------- the code search (in the colour popover) ----------

export function CodeSearch({ onPick }: { onPick: (ref: string) => void }) {
  const uid = useId()
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [active, setActive] = useState(0)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 150)
    return () => window.clearTimeout(t)
  }, [q])
  const books = useQuery({ queryKey: ['colourbooks'], queryFn: cbApi.list, staleTime: 60_000, retry: false })
  const found = useQuery({
    queryKey: ['colourbooks-lookup', debounced],
    queryFn: ({ signal }) => cbApi.lookup(debounced, 12, signal),
    enabled: debounced.length > 0,
    retry: false,
  })
  if (!books.data?.some((b) => b.enabled)) return null
  const list: FoundSwatch[] = found.data ?? []
  return (
    <div className="cbook-search">
      <label htmlFor={`${uid}-q`} className="section-label">
        <Palette size={14} aria-hidden="true" /> Or a code from your colour books
      </label>
      <input
        id={`${uid}-q`}
        role="combobox"
        aria-expanded={list.length > 0}
        aria-controls={`${uid}-list`}
        aria-activedescendant={list.length ? `${uid}-o${active}` : undefined}
        placeholder="RAL 7016, 19-4052…"
        value={q}
        spellCheck={false}
        onChange={(e) => {
          setQ(e.target.value)
          setActive(0)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((a) => Math.min(a + 1, list.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((a) => Math.max(a - 1, 0))
          } else if (e.key === 'Enter' && list[active]) {
            e.preventDefault()
            e.stopPropagation()
            onPick(list[active].ref)
          }
        }}
      />
      {list.length > 0 && (
        <ul id={`${uid}-list`} className="cbook-results" role="listbox" aria-label="Codes">
          {list.map((s, i) => (
            <li key={s.ref} role="presentation">
              <button
                id={`${uid}-o${i}`}
                type="button"
                role="option"
                aria-selected={i === active}
                tabIndex={-1}
                onMouseEnter={() => setActive(i)}
                onClick={() => onPick(s.ref)}
              >
                <span className="cbook-chip" style={{ background: s.hex }} aria-hidden="true" />
                <span>{swatchLabel(s)}</span>
                <span className="cbook-code-name">{s.book}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {debounced && found.isSuccess && list.length === 0 && <p className="hint">No code like that in your colour books.</p>}
    </div>
  )
}

// ---------- the search chip for a code ----------

/** The swatch and words for a colour search by code ('book:ral/RAL 7016'). */
export function useCodeChip(value: string | null): { hex: string | null; label: string } | null {
  const ref = value ? parseRef(value) : null
  const found = useQuery({
    queryKey: ['colourbooks-lookup', value],
    queryFn: ({ signal }) => cbApi.lookup(value as string, 1, signal),
    enabled: !!ref,
    staleTime: Infinity,
    retry: false,
  })
  if (!ref) return null
  const s = found.data?.[0]
  return { hex: s?.hex ?? null, label: s ? `${swatchLabel(s)} (${s.book})` : ref.code }
}

export function CodeChipText({ value }: { value: string }) {
  const chip = useCodeChip(value)
  if (!chip) return <>colour {value}</>
  return (
    <>
      {chip.hex && <span className="chip-swatch" style={{ background: chip.hex }} aria-hidden="true" />}
      colour {chip.label}
    </>
  )
}
