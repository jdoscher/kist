// F1 utility collections ("Kinds") in the library: the sidebar section,
// the kind bar over the grid (review modes, strictness, Not this / Belongs
// here with X and B), and the kinds block in the info panel and the phone
// item sheet. Settings → Kinds is KindsSettings.tsx (lazy).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, RotateCcw, Shapes, Settings2, X } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { api, emptyQuery, type Item } from '../../api'
import { useAfterUndo, useCaps, useCurrentItems } from '../../data'
import {
  kindFilterValue,
  kindName,
  kindsApi,
  learningText,
  leavesList,
  parseKindFilter,
  sidebarKinds,
  strictnessText,
  type ItemKind,
  type KindMode,
} from '../../kinds'
import { useAppState } from '../../state'
import type { KeyLike } from '../../shortcuts'
import { useShortcut } from '../../useShortcuts'
import { describeError } from '../../writes'
import { useNotices } from '../Notices'

const ICON = { size: 16 } as const

export function useKinds() {
  const caps = useCaps()
  return useQuery({ queryKey: ['kinds'], queryFn: kindsApi.overview, enabled: caps.ready, staleTime: 30_000, refetchInterval: 60_000 })
}

function useItemKinds(id: string | null) {
  return useQuery({ queryKey: ['itemKinds', id], queryFn: () => kindsApi.itemKinds(id as string), enabled: !!id })
}

/** "Not this" / "Belongs here" / back to automatic, with an Undo notice. */
export function useCorrect() {
  const qc = useQueryClient()
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  const { data: overview } = useKinds()
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['kinds'] })
    void qc.invalidateQueries({ queryKey: ['itemKinds'] })
    void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
    void qc.invalidateQueries({ queryKey: ['history'] })
    void qc.invalidateQueries({ queryKey: ['batches'] })
  }
  return async (kind: string, ids: string[], verdict: boolean | null) => {
    if (!ids.length) return
    const name = kindName(kind, overview)
    try {
      const r = await kindsApi.correct(kind, ids, verdict)
      refresh()
      const n = ids.length === 1 ? 'It' : `${ids.length} items`
      const message =
        r.changed === 0
          ? verdict === false
            ? `Already out of ${name}.`
            : verdict
              ? `Already in ${name}.`
              : `${name}: already automatic.`
          : verdict === false
            ? `Not ${name}: ${n.toLowerCase() === 'it' ? 'it stays out' : `${ids.length} items stay out`}.`
            : verdict
              ? `${n} ${ids.length === 1 ? 'belongs' : 'belong'} in ${name}.`
              : `${name}: back to automatic.`
      notify({
        key: 'kinds',
        message,
        action:
          r.changed > 0
            ? {
                label: 'Undo',
                run: () =>
                  void api.undoBatch(r.batch_id).then(
                    () => {
                      afterUndo()
                      refresh()
                    },
                    (e) => notify({ key: 'kinds', tone: 'error', message: `Couldn't undo: ${describeError(e)}` }),
                  ),
              }
            : undefined,
      })
    } catch (e) {
      notify({ key: 'kinds', tone: 'error', message: `Couldn't correct ${name}: ${describeError(e)}` })
    }
  }
}

// ---------- sidebar ----------

export function SidebarKinds() {
  const { query, setQuery, screen, openSettings } = useAppState()
  const caps = useCaps()
  const { data } = useKinds()
  const current = parseKindFilter(query.auto_kind)
  const rows = sidebarKinds(data, current && !current.not ? current.id : null)
  if (!data || !data.enabled) return null
  return (
    <>
      <div className="section-head" style={{ marginTop: 16 }}>
        <h2 id="sidebar-kinds-head" title="Kinds of picture Kist sorts out by itself; correct it and it learns">
          Kinds
        </h2>
        {caps.isAdmin && (
          <button className="icon-btn" onClick={() => openSettings('kinds')} title="Kinds settings: trade packs, strictness" aria-label="Kinds settings">
            <Settings2 size={14} aria-hidden="true" />
          </button>
        )}
      </div>
      <div role="group" aria-labelledby="sidebar-kinds-head">
        {rows.map((k) => {
          const active = !!current && !current.not && current.id === k.id && !screen
          return (
            <div key={k.id} className={`row kd-row${active ? ' active' : ''}${k.depth ? ' kd-child' : ''}`}>
              <button
                className="row-main"
                aria-current={active ? 'page' : undefined}
                title={k.how}
                onClick={() => setQuery(active ? { auto_kind: null } : { ...emptyQuery, order: query.order, auto_kind: k.id })}
              >
                <span className="icon" aria-hidden="true">
                  {k.depth ? <span className="kd-branch" /> : <Shapes {...ICON} />}
                </span>
                <span className="name">{k.name}</span>
                {k.count > 0 && (
                  <span className="count">
                    <span className="sr-only">, </span>
                    {k.count}
                    <span className="sr-only">{k.count === 1 ? ' item' : ' items'}</span>
                  </span>
                )}
              </button>
            </div>
          )
        })}
        {rows.length === 0 && (
          <div className="hint">
            {data.pending_jobs > 0
              ? 'Sorting…'
              : `A kind shows here once it holds ${data.min_items} items.`}
          </div>
        )}
      </div>
    </>
  )
}

// ---------- the kind bar over the grid ----------

const MODES: { mode: KindMode | null; label: string; title: string }[] = [
  { mode: null, label: 'All', title: 'Everything in this kind' },
  { mode: 'unsure', label: 'To review', title: 'Calls near the line, not yet corrected: say yes (B) or no (X)' },
  { mode: 'confirmed', label: 'Confirmed', title: 'Items you said belong here' },
  { mode: 'rejected', label: 'Ruled out', title: 'Items you said are not this kind' },
]

export function KindBar() {
  const { query, setQuery, selection, previewId, setPreviewId, settings, screen } = useAppState()
  const caps = useCaps()
  const { data } = useKinds()
  const { items } = useCurrentItems()
  const correct = useCorrect()
  const qc = useQueryClient()
  const { notify } = useNotices()
  const f = parseKindFilter(query.auto_kind)
  const kind = f && !f.not ? data?.kinds.find((k) => k.id === f.id) : undefined
  const [strict, setStrict] = useState<number | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const sliderId = useId()
  useEffect(() => setStrict(null), [kind?.id, kind?.strictness])

  const on = !!f && !f.not && !settings && !screen
  const target = (): string[] => (previewId ? [previewId] : [...selection])

  const act = (verdict: boolean) => {
    if (!f) return
    const ids = target()
    if (!ids.length) return
    // in the full-screen preview, move on when the item leaves this list
    if (previewId && leavesList(f.mode, verdict)) {
      const i = items.findIndex((it) => it.id === previewId)
      const next = items[i + 1] ?? items[i - 1]
      setPreviewId(next && next.id !== previewId ? next.id : null)
    }
    void correct(f.id, ids, verdict)
  }

  const keyHandler = (e: KeyLike) => {
    if (!on || !caps.canEdit || e.metaKey || e.ctrlKey || e.altKey) return false
    const t = e.target as HTMLElement | null
    if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return false
    const k = e.key.toLowerCase()
    if (k !== 'x' && k !== 'b') return false
    if (!target().length) return false
    e.preventDefault()
    act(k === 'b')
    return true
  }
  useShortcut('grid', keyHandler, on && caps.canEdit)
  useShortcut('preview', keyHandler, on && caps.canEdit)

  if (!on || !f) return null
  const value = strict ?? kind?.strictness ?? 0.5
  const saveStrict = (v: number) => {
    setStrict(v)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      kindsApi.patchKind(f.id, { strictness: v }).then(
        () => {
          void qc.invalidateQueries({ queryKey: ['kinds'] })
          void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
        },
        (e) => notify({ key: 'kinds', tone: 'error', message: `Couldn't save the strictness: ${describeError(e)}` }),
      )
    }, 350)
  }
  const n = target().length
  return (
    <div className="kd-bar" role="region" aria-label={`${kindName(f.id, data)}: review`}>
      <div className="seg kd-modes" role="group" aria-label="Show">
        {MODES.map((m) => (
          <button
            key={m.label}
            className={f.mode === m.mode ? 'on' : ''}
            aria-pressed={f.mode === m.mode}
            title={m.title}
            onClick={() => setQuery({ auto_kind: kindFilterValue({ id: f.id, not: false, mode: m.mode }) })}
          >
            {m.label}
          </button>
        ))}
      </div>
      {kind && caps.isAdmin && kind.clip && (
        <div className="kd-strict">
          <label htmlFor={sliderId}>Strictness</label>
          <input
            id={sliderId}
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={value}
            aria-valuetext={strictnessText(value)}
            onChange={(e) => saveStrict(Number(e.target.value))}
          />
          <span className="kd-dim">{strictnessText(value)}</span>
        </div>
      )}
      {kind && <span className="kd-dim kd-learn">{learningText(kind)}</span>}
      {caps.canEdit && (
        <div className="kd-act">
          <button disabled={!n} onClick={() => act(false)} title="Not this: it stays out of this kind (X)">
            <X size={14} aria-hidden="true" /> Not this <kbd>X</kbd>
          </button>
          <button disabled={!n} onClick={() => act(true)} title="Belongs here: confirm it (B)">
            <Check size={14} aria-hidden="true" /> Belongs here <kbd>B</kbd>
          </button>
        </div>
      )}
    </div>
  )
}

// ---------- the info panel / item sheet ----------

function chipTitle(k: ItemKind): string {
  const who = k.source === 'human' ? (k.verdict ? 'You confirmed it' : 'You ruled it out') : 'Kist sorted it here; not yet confirmed'
  const why = k.why ? `${k.why}${k.score !== null && k.score !== undefined && !k.why.includes(k.score.toFixed(2)) ? ` (${k.score.toFixed(2)})` : ''}` : ''
  return [who, why].filter(Boolean).join('. ')
}

export function ItemKinds({ item }: { item: Item }) {
  const caps = useCaps()
  const { data: overview } = useKinds()
  const { data } = useItemKinds(item.id)
  const correct = useCorrect()
  const { query, setQuery } = useAppState()
  const selectId = useId()
  const list = data ?? []
  const addable = useMemo(
    () => (overview?.kinds ?? []).filter((k) => k.enabled && !list.some((x) => x.kind === k.id && x.member)),
    [overview, list],
  )
  if (!overview?.enabled || (!list.length && !caps.canEdit)) return null
  return (
    <div className="detail-section kd-item">
      <label htmlFor={caps.canEdit ? selectId : undefined}>Kinds</label>
      <div className="tag-chips">
        {list.map((k) => (
          <span
            key={k.kind}
            className={`chip kd-chip${k.source === 'auto' ? ' kd-auto' : ''}${k.member ? '' : ' kd-out'}`}
            title={chipTitle(k)}
          >
            <button className="kd-chip-name" onClick={() => setQuery({ ...emptyQuery, order: query.order, auto_kind: k.kind })} aria-label={`Show ${k.name}`}>
              {k.member ? k.name : `Not ${k.name}`}
              {k.source === 'auto' && <span className="kd-tag">auto</span>}
            </button>
            {caps.canEdit && k.member && k.source === 'auto' && (
              <button aria-label={`Confirm ${k.name}`} title="Confirm: it belongs here" onClick={() => void correct(k.kind, [item.id], true)}>
                <Check size={14} aria-hidden="true" />
              </button>
            )}
            {caps.canEdit && k.member && (
              <button aria-label={`Not ${k.name}`} title="Not this: keep it out of this kind" onClick={() => void correct(k.kind, [item.id], false)}>
                <X size={14} aria-hidden="true" />
              </button>
            )}
            {caps.canEdit && !k.member && (
              <button aria-label={`Back to automatic for ${k.name}`} title="Back to automatic" onClick={() => void correct(k.kind, [item.id], null)}>
                <RotateCcw size={14} aria-hidden="true" />
              </button>
            )}
          </span>
        ))}
        {!list.length && <span className="kd-dim">None yet</span>}
      </div>
      {caps.canEdit && addable.length > 0 && (
        <select
          id={selectId}
          value=""
          onChange={(e) => {
            if (e.target.value) void correct(e.target.value, [item.id], true)
          }}
        >
          <option value="">Belongs in a kind…</option>
          {addable.map((k) => (
            <option key={k.id} value={k.id}>
              {k.parent ? `  ${k.name}` : k.name}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
