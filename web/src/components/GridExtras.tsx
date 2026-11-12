// What Milestone 2 adds to the grid: why a ranked result matched (a badge
// and the snippet with its words marked), stack badges with expand in
// place, the item context menu (More like this, its colour) and the note
// above a ranked or degraded search.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Layers, Maximize2, Palette, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { Item, ItemPage, Query } from '../api'
import { useCurrentItems } from '../data'
import { org } from '../organiseApi'
import { colourSearch, isRanked, matchLabel, moreLikeThis, parseSnippet, rankedErrorText, snippetText } from '../search'
export { rankedErrorText, showMatchBadge } from '../search'
import { closeStack, getExpanded, openStack, stackBadge, subscribeExpanded } from '../stacks'
import { useAppState } from '../state'
import { describeError } from '../writes'
import { useNotices } from './Notices'

/** A snippet with the matched words highlighted. */
export function Snippet({ text, className = '' }: { text: string | null | undefined; className?: string }) {
  const parts = parseSnippet(text)
  if (!parts.length) return null
  return (
    <span className={`snippet ${className}`}>
      {parts.map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
    </span>
  )
}

/** On a tile: the match kind as a badge (see showMatchBadge) and, for text
 * hits, the snippet with the words marked. */
export function TileMatch({ item, badge }: { item: Item; badge: boolean }) {
  const { query } = useAppState()
  const m = item.match
  if (!m) return null
  return (
    <>
      {badge && (
        <span className={`badge match match-${m.kind}`} aria-hidden="true">
          {matchLabel(m.kind, query)}
        </span>
      )}
      {/* a space, so the badge and snippet read as separate words */}{' '}
      {m.snippet && (
        <div className="tile-snippet" aria-hidden="true">
          <Snippet text={m.snippet} />
        </div>
      )}
    </>
  )
}

/** A person's name for headings ("Unnamed person" until named). */
export function usePersonName(id: string | null): string | null {
  const { data } = useQuery({ queryKey: ['people'], queryFn: org.people, enabled: !!id, retry: false })
  if (!id) return null
  const p = data?.find((x) => x.id === id)
  return p ? (p.name ?? 'Unnamed person') : null
}

export function useExpandedStacks() {
  return useSyncExternalStore(subscribeExpanded, getExpanded)
}

/** Open or close a stack in place (members after its entry). */
export function useStackToggle() {
  const { notify } = useNotices()
  const expanded = useExpandedStacks()
  return useCallback(
    async (item: Pick<Item, 'stack_id' | 'filename'>) => {
      const sid = item.stack_id
      if (!sid) return
      if (expanded.has(sid)) {
        closeStack(sid)
        return
      }
      try {
        const st = await org.stack(sid)
        openStack(sid, st.items)
      } catch (e) {
        notify({ tone: 'error', key: 'stack', message: `Couldn't open the stack: ${describeError(e)}` })
      }
    },
    [expanded, notify],
  )
}

/** The count badge on a stack's entry (or a marker on an opened member). */
export function StackBadge({ item, member }: { item: Item; member: boolean }) {
  const expanded = useExpandedStacks()
  const open = !!item.stack_id && expanded.has(item.stack_id)
  const b = stackBadge(item.stack_count, open)
  if (member)
    return (
      <span className="badge stack member" aria-hidden="true" title="In an open stack">
        <Layers size={14} />
      </span>
    )
  if (!b) return null
  return (
    <span className={`badge stack${open ? ' open' : ''}`} aria-hidden="true" data-stack-toggle="" title={`${b.label} (S)`}>
      <Layers size={14} />
      {b.text}
    </span>
  )
}

/** Extra words for a tile's accessible name. */
export function tileExtra(item: Item, member: boolean, open: boolean, q?: Query): string {
  const parts: string[] = []
  if (item.match) {
    parts.push(`matched by ${matchLabel(item.match.kind, q).toLowerCase()}`)
    // the snippet shown on the tile is part of its name
    if (item.match.snippet) parts.push(snippetText(item.match.snippet))
  }
  if (!member && item.stack_count && item.stack_count > 1)
    parts.push(`stack of ${item.stack_count} items, ${open ? 'open' : 'press S to open'}`)
  if (member) parts.push('in an open stack')
  return parts.length ? `, ${parts.join(', ')}` : ''
}

/** The note above a ranked search: how it's ordered, or that meaning
 * search fell back to words. */
export function SearchNote() {
  const { query } = useAppState()
  const { result } = useCurrentItems()
  const degraded = (result.data?.pages[0] as ItemPage | undefined)?.degraded
  if (degraded === 'meaning')
    return (
      <p className="view-subtitle search-note">
        Searching by meaning isn’t available right now (the AI service isn’t answering), so these are word matches
        only.
      </p>
    )
  if (!isRanked(query)) return null
  return <p className="view-subtitle search-note">Best matches first.</p>
}

/** The tile context menu: open, more like this, search its main colour. */
export function ItemContextMenu({
  item,
  x,
  y,
  onClose,
}: {
  item: Item
  x: number
  y: number
  onClose: () => void
}) {
  const { query, setQuery, setPreviewId } = useAppState()
  const toggleStack = useStackToggle()
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ x, y })
  // the tile (or whatever) it was opened from: Escape puts focus back there
  // instead of dropping it on the page, where the grid's keys stopped working
  const opener = useRef<HTMLElement | null>(null)
  if (opener.current === null && typeof document !== 'undefined') opener.current = document.activeElement as HTMLElement | null
  const top = item.dominant_colors?.[0]?.hex ?? null
  useEffect(() => {
    const el = ref.current
    if (el) {
      const r = el.getBoundingClientRect()
      setPos({
        x: Math.max(4, Math.min(x, window.innerWidth - r.width - 4)),
        y: Math.max(4, Math.min(y, window.innerHeight - r.height - 4)),
      })
      el.querySelector<HTMLElement>('[role=menuitem]')?.focus()
    }
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    window.addEventListener('mousedown', close)
    window.addEventListener('blur', onClose)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('blur', onClose)
    }
  }, [x, y, onClose])
  const run = (fn: () => void) => () => {
    onClose()
    fn()
  }
  const items: { label: string; icon: typeof Sparkles; run: () => void; hide?: boolean }[] = [
    { label: 'Open full screen', icon: Maximize2, run: () => setPreviewId(item.id) },
    { label: 'More like this', icon: Sparkles, run: () => setQuery(moreLikeThis(item.id, query)), hide: !item.has_thumb },
    {
      label: top ? `Find this colour (${top})` : 'Find this colour',
      icon: Palette,
      run: () => top && setQuery(colourSearch(top, query)),
      hide: !top,
    },
    {
      label: 'Open or close its stack',
      icon: Layers,
      run: () => void toggleStack(item),
      hide: !item.stack_id || !item.stack_count || item.stack_count < 2,
    },
  ]
  const shown = items.filter((i) => !i.hide)
  return (
    <div
      ref={ref}
      className="ctx-menu"
      role="menu"
      aria-label={`Actions for ${item.filename}`}
      style={{ left: pos.x, top: pos.y }}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => {
        const els = [...(ref.current?.querySelectorAll<HTMLElement>('[role=menuitem]') ?? [])]
        const i = els.indexOf(document.activeElement as HTMLElement)
        if (e.key === 'Escape' || e.key === 'Tab') {
          e.preventDefault()
          e.stopPropagation()
          onClose()
          if (opener.current?.isConnected) opener.current.focus()
        } else if (e.key === 'ArrowDown') {
          e.preventDefault()
          els[(i + 1) % els.length]?.focus()
        } else if (e.key === 'ArrowUp') {
          e.preventDefault()
          els[(i - 1 + els.length) % els.length]?.focus()
        }
      }}
    >
      {shown.map((m) => (
        <button key={m.label} type="button" role="menuitem" tabIndex={-1} onClick={run(m.run)}>
          <m.icon size={16} aria-hidden="true" />
          {m.label}
        </button>
      ))}
    </div>
  )
}

/** After stacking or unstacking: lists reload (the server decides which
 * entry stands for a stack), open stacks close. */
export function useAfterStackChange() {
  const qc = useQueryClient()
  return useCallback(() => {
    for (const sid of getExpanded().keys()) closeStack(sid)
    void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
    void qc.invalidateQueries({ queryKey: ['item'] })
    void qc.invalidateQueries({ queryKey: ['stackSuggestions'] })
    void qc.invalidateQueries({ queryKey: ['batches'] })
  }, [qc])
}

export function RankedError({ status }: { status: number | undefined }) {
  const { query, setQuery } = useAppState()
  const text = rankedErrorText(status, query)
  if (!text) return null
  const words = query.semantic || query.text
  return (
    <div className="grid-empty" role="alert">
      <p>{text}</p>
      {words && status === 503 && (
        <button onClick={() => setQuery({ q: words, semantic: '', text: '', similar: null, image: null, color: null })}>
          Search the words instead
        </button>
      )}
    </div>
  )
}
