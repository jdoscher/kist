// Milestone 2 in the info panel and the phone sheet: why an item matched,
// More like this, its palette (click a colour to search it), the caption
// (editable; regenerate when a provider is set), tag suggestions, implied
// tags, namespaced tag chips, faces and its stack.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Layers, Plus, RefreshCw, Sparkles, TriangleAlert, Users, Wand2, X } from 'lucide-react'
import { useEffect, useId, useMemo, useState } from 'react'
import { api, emptyQuery, newBatchId, type Item } from '../api'
import { useApplyItems, useCaps } from '../data'
import { org, type TagSuggestion } from '../organiseApi'
import { hexLabelled } from '../format'
import { colourSearch, matchLabel, moreLikeThis } from '../search'
import { useAppState } from '../state'
import { impliedBy, splitTag } from '../tagNs'
import { useDraftField, useItemWrite } from '../useWrite'
import { describeError } from '../writes'
import { Snippet, useAfterStackChange, useExpandedStacks, useStackToggle } from './GridExtras'
import { useNotices } from './Notices'
import { useDialog } from './Dialog'

/** What the server has switched on (AI status is the owner's; others
 * learn about faces from whether any people exist). */
export function useAiFlags() {
  const caps = useCaps()
  const status = useQuery({
    queryKey: ['aiStatus'],
    queryFn: api.aiStatus,
    enabled: caps.ready && caps.isAdmin,
    staleTime: 60_000,
    retry: false,
  })
  const people = useQuery({
    queryKey: ['people'],
    queryFn: org.people,
    enabled: caps.ready && !caps.isAdmin,
    staleTime: 60_000,
    retry: false,
  })
  const s = status.data
  return {
    faces: s ? !!s.faces : (people.data?.length ?? 0) > 0,
    captionProvider: s?.captions?.provider ?? null,
    captionWarning: s?.captions?.leaves_house ? (s.captions.warning ?? 'Images leave your network.') : null,
    ready: caps.isAdmin ? status.isFetched : people.isFetched,
  }
}

/** A tag as a chip label: "brand:rolex" shows as two parts. */
export function TagText({ tag, hash = true }: { tag: string; hash?: boolean }) {
  const { namespace, value } = splitTag(tag)
  if (!namespace) return <>{hash ? `#${tag}` : tag}</>
  return (
    <span className="ns-tag">
      <span className="ns-part">{namespace}</span>
      <span className="sr-only">: </span>
      <span className="ns-value">{value}</span>
    </span>
  )
}

/** Why it's in this search: the kind, the score, the words found. */
export function MatchWhy({ item }: { item: Item }) {
  const { query } = useAppState()
  const m = item.match
  if (!m) return null
  return (
    <div className="detail-section match-why">
      <h3 className="section-label">Why it’s here</h3>
      <p>
        <span className={`match-pill match-${m.kind}`}>{matchLabel(m.kind, query)}</span>{' '}
        <span className="hint-inline">score {m.score.toFixed(3)}</span>
      </p>
      {m.snippet && (
        <p className="match-snippet">
          <Snippet text={m.snippet} />
        </p>
      )}
    </div>
  )
}

export function MoreLikeThisButton({ item, className = '' }: { item: Item; className?: string }) {
  const { query, setQuery } = useAppState()
  if (!item.has_thumb || item.state !== 'active') return null
  return (
    <button
      type="button"
      className={`btn-icon more-like ${className}`}
      onClick={() => setQuery(moreLikeThis(item.id, query))}
      title="Find items that look like this one"
    >
      <Sparkles size={14} aria-hidden="true" />
      More like this
    </button>
  )
}

/** The item's colours; click one to find items with it. */
export function PaletteSwatches({ item }: { item: Item }) {
  const { query, setQuery } = useAppState()
  const { data } = useQuery({
    queryKey: ['palette', item.id, item.preview_rev],
    queryFn: () => org.palette(item.id),
    enabled: !!item.dominant_colors?.length,
    staleTime: Infinity,
    retry: false,
  })
  const colours = data ?? (item.dominant_colors ?? []).map((c) => ({ ...c, name: '' }))
  if (!colours.length) return null
  const pcts = colours.map((c) => Math.round(c.pct <= 1 ? c.pct * 100 : c.pct))
  const labelled = hexLabelled(pcts)
  // one continuous strip, each colour as wide as its share (B14); the hex
  // row underneath repeats the widths so a label sits under its colour
  return (
    <div className="detail-section">
      <h3 className="section-label">Colours</h3>
      <div className="color-palette" role="group" aria-label="Colours: press one to find items with it">
        {colours.map((c, i) => {
          const pct = pcts[i]
          const name = c.name ? `${c.name} ` : ''
          return (
            <button
              key={c.hex}
              type="button"
              className="palette-swatch"
              style={{ background: c.hex, flexGrow: Math.max(1, pct) }}
              aria-label={`Find ${name}${c.hex} (${pct}%)`}
              title={`${name}${c.hex} · ${pct}% — find items with this colour`}
              onClick={() => setQuery(colourSearch(c.hex, query))}
            />
          )
        })}
      </div>
      {labelled.some(Boolean) && (
        <div className="palette-hex mono" aria-hidden="true">
          {colours.map((c, i) => (
            <span key={c.hex} style={{ flexGrow: Math.max(1, pcts[i]) }}>
              {labelled[i] ? c.hex : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/** The caption: a sentence about the image, editable; regenerate it when a
 * caption provider is set up. */
export function CaptionField({ item, editable }: { item: Item; editable: boolean }) {
  const uid = useId()
  const flags = useAiFlags()
  const { write, busy } = useItemWrite(item.id)
  const caption = useDraftField(item.id, 'caption', item.caption ?? '')
  const { notify } = useNotices()
  const apply = useApplyItems()
  const [waitFor, setWaitFor] = useState<string | null>(null) // the caption before regenerating
  const [warned, setWarned] = useState(false)
  const dialog = useDialog()

  // after Regenerate: check the item every few seconds until the caption changes
  const poll = useQuery({
    queryKey: ['captionPoll', item.id],
    queryFn: () => api.getItem(item.id),
    enabled: waitFor !== null,
    refetchInterval: 3000,
  })
  useEffect(() => {
    if (waitFor === null || !poll.data) return
    if ((poll.data.caption ?? '') !== waitFor) {
      setWaitFor(null)
      apply([poll.data])
    }
  }, [poll.data, waitFor, apply])
  useEffect(() => {
    if (waitFor === null) return
    const t = setTimeout(() => {
      setWaitFor(null)
      notify({ key: 'caption', message: 'The caption is still being written; it will show up here when it’s done.' })
    }, 120_000)
    return () => clearTimeout(t)
  }, [waitFor, notify])

  if (!editable && !item.caption) return null

  const save = () => {
    if (!caption.dirty) return
    const sent = caption.value
    void write('caption', () => api.patchItem(item.id, { caption: sent.trim() }), () => caption.saved(sent))
  }
  const regenerate = async () => {
    if (flags.captionWarning && !warned) {
      const ok = await dialog.confirm({
        title: 'Send this image to be described?',
        message: flags.captionWarning,
        confirmLabel: 'Send it',
      })
      if (!ok) return
      setWarned(true)
    }
    try {
      await org.caption(item.id)
      setWaitFor(item.caption ?? '')
    } catch (e) {
      notify({ key: 'caption', tone: 'error', message: `Couldn't ask for a caption: ${describeError(e)}` })
    }
  }

  if (!editable)
    return (
      <div className="detail-section">
        <h3 className="section-label">Caption</h3>
        <p className="caption-text">{item.caption}</p>
      </div>
    )

  return (
    <div className="detail-section caption-field">
      <div className="section-row">
        <label htmlFor={`${uid}-cap`}>
          Caption{caption.dirty && <span className="unsaved-note"> · not saved yet</span>}
        </label>
        {flags.captionProvider && item.kind === 'image' && (
          <button
            type="button"
            className="btn-icon small"
            onClick={() => void regenerate()}
            disabled={waitFor !== null}
            title={`Write a new caption with ${flags.captionProvider}${flags.captionWarning ? ' (the image is sent to them)' : ''}`}
          >
            <RefreshCw size={14} aria-hidden="true" className={waitFor !== null ? 'spin' : ''} />
            {waitFor !== null ? 'Writing…' : item.caption ? 'Regenerate' : 'Describe'}
          </button>
        )}
      </div>
      <textarea
        id={`${uid}-cap`}
        rows={2}
        value={caption.value}
        maxLength={2000}
        placeholder={flags.captionProvider ? 'No caption yet. Write one, or press Describe.' : 'A sentence about the image (searchable)'}
        onChange={(e) => caption.change(e.target.value)}
        onBlur={save}
        aria-describedby={flags.captionWarning ? `${uid}-capwarn` : undefined}
      />
      {busy('caption') && <span className="hint-inline">saving…</span>}
      {flags.captionWarning && flags.captionProvider && (
        <p id={`${uid}-capwarn`} className="cap-warn">
          <TriangleAlert size={14} aria-hidden="true" /> {flags.captionWarning}
        </p>
      )}
    </div>
  )
}

const WHY_TEXT: Record<string, string> = {
  'similar items': 'is on items that look like this',
  'matches the image': 'matches what’s in the picture',
}

/** Suggested tags under the tag box: add one, or say no (it won't be
 * suggested for this item again). Never applied by themselves. */
export function TagSuggestions({ item }: { item: Item }) {
  const qc = useQueryClient()
  const { write } = useItemWrite(item.id)
  const { notify } = useNotices()
  const key = ['tagSuggestions', item.id, item.tags.join('\u0001')]
  const { data, isError } = useQuery({
    queryKey: key,
    queryFn: () => org.tagSuggestions(item.id, 6),
    enabled: item.state === 'active' && item.has_thumb,
    staleTime: 60_000,
    retry: false,
  })
  const [gone, setGone] = useState<Set<string>>(new Set())
  const list = (data ?? []).filter((s) => !gone.has(s.tag.toLowerCase()) && !item.tags.some((t) => t.toLowerCase() === s.tag.toLowerCase()))
  if (isError || !list.length) return null
  const hide = (s: TagSuggestion) => setGone((g) => new Set(g).add(s.tag.toLowerCase()))
  const accept = async (s: TagSuggestion) => {
    hide(s)
    const ok = await write('tags', () => api.patchTags(item.id, { add: [s.tag] }))
    if (ok) void org.tagFeedback(item.id, s.tag, true).catch(() => {})
  }
  const reject = (s: TagSuggestion) => {
    hide(s)
    org.tagFeedback(item.id, s.tag, false).then(
      () => qc.invalidateQueries({ queryKey: ['tagSuggestions', item.id] }),
      (e) => notify({ tone: 'error', message: `Couldn't save that: ${describeError(e)}` }),
    )
  }
  return (
    <div className="tag-suggest" role="group" aria-label="Suggested tags">
      <span className="tag-suggest-label">
        <Wand2 size={14} aria-hidden="true" /> Suggested
      </span>
      {list.map((s) => {
        const why = WHY_TEXT[s.why] ?? s.why
        return (
          <span key={s.tag} className={`suggest-chip why-${s.why === 'similar items' ? 'similar' : 'image'}`} title={`Suggested because it ${why} (${Math.round(s.score * 100)}%)`}>
            <button type="button" className="suggest-add" onClick={() => void accept(s)}>
              <Plus size={14} aria-hidden="true" />
              <span className="sr-only">Add tag </span>
              <TagText tag={s.tag} hash={false} />
              <span className="suggest-why">{s.why === 'similar items' ? 'similar' : 'image'}</span>
              <span className="sr-only">, suggested because it {why}</span>
            </button>
            <button type="button" className="suggest-no" onClick={() => reject(s)} aria-label={`Not ${s.tag}: don’t suggest it for this item`} title="Not this: don’t suggest it again for this item">
              <X size={14} aria-hidden="true" />
            </button>
          </span>
        )
      })}
    </div>
  )
}

/** Tags the vocabulary implies, greyed: searched for, never stored. */
export function ImpliedTags({ item }: { item: Item }) {
  const implied = item.implied_tags ?? []
  const { data } = useQuery({
    queryKey: ['vocabulary'],
    queryFn: org.vocabulary,
    enabled: implied.length > 0,
    staleTime: 60_000,
    retry: false,
  })
  const by = useMemo(() => impliedBy(item.tags, implied, data?.implies ?? []), [item.tags, implied, data])
  if (!implied.length) return null
  return (
    <div className="implied-tags">
      <span className="implied-label">Implied:</span>
      {implied.map((t) => (
        <span key={t} className="chip implied" title={by.get(t) ? `Implied by ${by.get(t)} (vocabulary); not stored on the item` : 'Implied by the vocabulary; not stored on the item'}>
          <TagText tag={t} />
          {by.get(t) && <span className="sr-only"> (implied by {by.get(t)})</span>}
        </span>
      ))}
    </div>
  )
}

/** Faces found in the item: who they are; name one; see their items. */
export function ItemFaces({ item, editable }: { item: Item; editable: boolean }) {
  const flags = useAiFlags()
  const { query, setQuery } = useAppState()
  const dialog = useDialog()
  const qc = useQueryClient()
  const { notify } = useNotices()
  const people = useQuery({ queryKey: ['people'], queryFn: org.people, enabled: flags.faces && editable, retry: false })
  const { data } = useQuery({
    queryKey: ['faces', item.id],
    queryFn: () => org.faces(item.id),
    enabled: flags.faces && item.kind === 'image',
    retry: false,
  })
  if (!flags.faces || !data?.length) return null
  const move = async (faceId: number, personId: string | null) => {
    try {
      await org.patchFace(faceId, personId)
      void qc.invalidateQueries({ queryKey: ['faces'] })
      void qc.invalidateQueries({ queryKey: ['people'] })
    } catch (e) {
      notify({ tone: 'error', message: `Couldn't move the face: ${describeError(e)}` })
    }
  }
  const name = async (personId: string, current: string | null) => {
    const v = await dialog.prompt({ title: current ? `Rename ${current}` : 'Who is this?', label: 'Name', initial: current ?? '', confirmLabel: 'Save' })
    if (!v) return
    try {
      await org.patchPerson(personId, { name: v })
      void qc.invalidateQueries({ queryKey: ['faces'] })
      void qc.invalidateQueries({ queryKey: ['people'] })
    } catch (e) {
      notify({ tone: 'error', message: `Couldn't save the name: ${describeError(e)}` })
    }
  }
  return (
    <div className="detail-section">
      <h3 className="section-label">
        <Users size={14} aria-hidden="true" /> People
      </h3>
      <ul className="item-faces">
        {data.map((f) => (
          <li key={f.id}>
            <button
              type="button"
              className="face-btn"
              disabled={!f.person_id}
              onClick={() => f.person_id && setQuery({ ...emptyQuery, order: query.order, person: f.person_id })}
              title={f.person_id ? `Show everything with ${f.person_name ?? 'this person'}` : 'Not matched to anyone yet'}
            >
              <img src={f.thumb_url} alt="" width={44} height={44} />
              <span>{f.person_name ?? 'Unnamed'}</span>
            </button>
            {editable && f.person_id && (
              <button type="button" className="link-btn" onClick={() => void name(f.person_id as string, f.person_name ?? null)}>
                {f.person_name ? 'Rename' : 'Name'}
                <span className="sr-only"> this person</span>
              </button>
            )}
            {editable && (people.data?.length ?? 0) > 0 && (
              <select
                className="face-move"
                aria-label={`Who is face ${f.idx != null ? f.idx + 1 : ''}? Move it to another person`}
                value={f.person_id ?? ''}
                onChange={(e) => void move(f.id, e.target.value || null)}
              >
                <option value="">Not matched</option>
                {(people.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name ?? `Unnamed (${p.face_count})`}
                  </option>
                ))}
              </select>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The item's stack: open it in the grid, make it the cover, take it out. */
export function StackInfo({ item, editable }: { item: Item; editable: boolean }) {
  const toggle = useStackToggle()
  const expanded = useExpandedStacks()
  const after = useAfterStackChange()
  const { notify } = useNotices()
  const { data } = useQuery({
    queryKey: ['stack', item.stack_id],
    queryFn: () => org.stack(item.stack_id as string),
    enabled: !!item.stack_id,
    retry: false,
  })
  if (!item.stack_id) return null
  const count = data?.count ?? item.stack_count ?? 0
  const isCover = data?.cover_item_id === item.id
  const run = async (what: string, fn: (batch: string) => Promise<unknown>) => {
    const batch = newBatchId()
    try {
      await fn(batch)
      after()
      notify({
        key: 'stack',
        message: `${what}.`,
        action: {
          label: 'Undo',
          run: () => void api.undoBatch(batch).then(after, (e) => notify({ tone: 'error', key: 'stack', message: `Couldn't undo: ${describeError(e)}` })),
        },
      })
    } catch (e) {
      notify({ tone: 'error', key: 'stack', message: `Couldn't change the stack: ${describeError(e)}` })
    }
  }
  const open = expanded.has(item.stack_id)
  return (
    <div className="detail-section stack-info">
      <h3 className="section-label">
        <Layers size={14} aria-hidden="true" /> Stack
      </h3>
      <p>
        One of {count} stacked items{isCover ? ' (the cover)' : ''}.
      </p>
      <div className="btn-row">
        <button type="button" onClick={() => void toggle(item)}>
          {open ? 'Close stack in grid' : 'Show all in grid'}
        </button>
        {editable && !isCover && data && (
          <button type="button" onClick={() => void run('Made it the cover', (b) => org.patchStack(item.stack_id as string, { cover: item.id }, b))}>
            <Check size={14} aria-hidden="true" /> Make cover
          </button>
        )}
        {editable && data && (
          <button type="button" onClick={() => void run('Took it out of the stack', (b) => org.patchStack(item.stack_id as string, { remove: [item.id] }, b))}>
            Take out
          </button>
        )}
      </div>
    </div>
  )
}
