// Milestone 4/5 controls in the top bar: chips for the provenance and media
// filters, the Filters button (its panel loads on first use), and Share /
// Export for the folder or smart folder on screen. Small on purpose: this
// file is part of the first page.

import { Download, Share2, SlidersHorizontal, X } from 'lucide-react'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import type { Query } from '../../api'
import { useFolderNames, useSmartFolders } from '../../data'
import { filterChipLabel, m45Filters, type M45FilterKey } from '../../m45/filters'
import { useAppState } from '../../state'
import { useM45 } from './M45'

const FiltersPanel = lazy(() => import('./FiltersPanel'))

export function M45Chips({ query, setQuery }: { query: Query; setQuery: (p: Partial<Query>) => void }) {
  const f = m45Filters(query)
  return (
    <>
      {(Object.entries(f) as [M45FilterKey, string][]).map(([k, v]) => {
        const label = filterChipLabel(k, v)
        return (
          <span key={k} className="chip">
            {label}
            <button aria-label={`Clear ${label}`} title={`Clear ${label}`} onClick={() => setQuery({ [k]: null } as Partial<Query>)}>
              <X size={13} strokeWidth={2} aria-hidden="true" />
            </button>
          </span>
        )
      })}
    </>
  )
}

export function FiltersButton({ mobile }: { mobile: boolean }) {
  const [open, setOpen] = useState(false)
  const { query } = useAppState()
  const n = Object.keys(m45Filters(query)).length
  return (
    <>
      <button
        type="button"
        className={`btn-icon filters-btn${n ? ' on' : ''}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        title="Filter by date taken, camera, place, length, licence…"
      >
        <SlidersHorizontal size={15} strokeWidth={1.75} aria-hidden="true" />
        <span className={mobile ? 'sr-only' : 'btn-text'}>Filters</span>
        {n > 0 && <span className="count-badge">{n}</span>}
      </button>
      {open && (
        <Suspense fallback={null}>
          <FiltersPanel onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </>
  )
}

/** Share or export what's on screen: a folder, a smart folder, or (export
 * only) the whole library. */
export function ViewShareMenu() {
  const { query, smartId } = useAppState()
  const { openShare, openExport } = useM45()
  const folderName = useFolderNames()
  const { data: smart } = useSmartFolders()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false)
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [open])
  const target = smartId
    ? { kind: 'smart' as const, id: smartId, itemIds: [], name: smart?.find((s) => s.id === smartId)?.name ?? 'smart folder' }
    : query.folder && query.folder !== 'none'
      ? { kind: 'folder' as const, id: query.folder, itemIds: [], name: folderName.get(query.folder) ?? 'folder' }
      : null
  const pick = (fn: () => void) => {
    setOpen(false)
    fn()
  }
  return (
    <div className="menu-wrap" ref={ref} onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}>
      <button
        type="button"
        className="icon-btn topbar-icon"
        aria-label={target ? `Share or export ${target.name}` : 'Export the library'}
        title={target ? `Share or export “${target.name}”` : 'Export the whole library'}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((o) => !o)}
      >
        <Share2 size={16} strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open && (
        <div className="menu" role="menu">
          {target && (
            <button role="menuitem" type="button" autoFocus onClick={() => pick(() => openShare(target))}>
              <Share2 size={14} aria-hidden="true" /> Share link…
            </button>
          )}
          <button role="menuitem" type="button" autoFocus={!target} onClick={() => pick(() => openExport(target ?? { kind: 'all', id: null, itemIds: [], name: '' }))}>
            <Download size={14} aria-hidden="true" /> {target ? 'Export…' : 'Export the whole library…'}
          </button>
        </div>
      )}
    </div>
  )
}
