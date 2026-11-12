// Triage mode (/triage?view=unfiled): one item at a time, big, with the
// queue of undecided items (the server skips flagged, labelled and rated
// ones in Unfiled, so "N left" counts down with each decision),
// culling keys (P/X/U, 0-5, 6-9, T tag, F folder, ←/→, ⌘Z) and, on a
// phone, swipes (up keep, down reject, left/right move) plus buttons for
// everything (WCAG 2.5.7: nothing needs a gesture).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Folder, Hash, Star, Trash2, Undo2, X } from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { api, type Item } from '../../api'
import { api3 } from '../../creativeApi'
import { advances, swipeAction, triageKey, type CullAction } from '../../cull'
import { useAfterUndo, useFolders, useTags } from '../../data'
import { swipeDirection } from '../../mobile'
import { matchScore } from '../../palette'
import { useAppState } from '../../state'
import type { Screen } from '../../url'
import { useLayer, useShortcut } from '../../useShortcuts'
import { describeError } from '../../writes'
import { Modal } from '../Dialog'
import { useNotices } from '../Notices'
import { cullText, FlagButtons, LabelPicker } from './CullControls'
import { displaySrc } from '../../look'
import { useFileItem, usePatchItem } from './writes3'

const AUTO_KEY = 'kist.triageAuto'

export default function Triage({ screen }: { screen: Extract<Screen, { kind: 'triage' }> }) {
  const { setQuery, mobile } = useAppState()
  const qc = useQueryClient()
  const afterUndo = useAfterUndo()
  const { notify } = useNotices()
  const { patch } = usePatchItem()
  const file = useFileItem()
  const { data: folders } = useFolders()
  const [cursor, setCursor] = useState<string | null>(null) // `after` for the current fetch
  const [trail, setTrail] = useState<string[]>([]) // items shown before, for ←
  // an item stepped back to (←) or undone, shown instead of the queue's
  const [pinned, setPinned] = useState<Item | null>(null)
  // items decided on this session (a flag, label, rating, tag, folder or
  // Trash): in the Unfiled queue each one leaves it, so "N left" goes down
  const [decided, setDecided] = useState<string[]>([])
  const done = decided.length
  const markDone = (id: string) => setDecided((d) => (d.includes(id) ? d : [...d, id]))
  const [batches, setBatches] = useState<{ batch: string; itemId: string }[]>([])
  const [auto, setAutoRaw] = useState(() => {
    try {
      return localStorage.getItem(AUTO_KEY) !== '0'
    } catch {
      return true
    }
  })
  const setAuto = (v: boolean) => {
    setAutoRaw(v)
    try {
      localStorage.setItem(AUTO_KEY, v ? '1' : '0')
    } catch {
      /* private */
    }
  }
  const [tagOpen, setTagOpen] = useState(false)
  const [folderOpen, setFolderOpen] = useState(false)
  const [said, setSaid] = useState('')
  const view = screen.view

  const next = useQuery({
    queryKey: ['triage', view, screen.folder ?? null, cursor],
    queryFn: () => api3.triageNext({ view, after: cursor, folder: screen.folder }),
    staleTime: 0,
    gcTime: 60_000,
  })

  // how many are left, refreshed after every decision (the queue's own
  // fetch stays put so the item on screen doesn't jump)
  const left = useQuery({
    queryKey: ['triage', 'left', view, screen.folder ?? null],
    queryFn: () => api3.triageNext({ view, folder: screen.folder, prefetch: 0 }),
    staleTime: 0,
  })
  const refreshLeft = () => void qc.invalidateQueries({ queryKey: ['triage', 'left'] })

  const current = pinned ?? next.data?.item ?? null
  // the latest copy of the current item (edits patch ['item', id])
  const item = current ? (qc.getQueryData<Item>(['item', current.id]) ?? current) : null
  const [, force] = useState(0)

  // prefetch the next few images
  useEffect(() => {
    for (const id of next.data?.prefetch_ids ?? []) {
      void qc.prefetchQuery({ queryKey: ['item', id], queryFn: () => api.getItem(id), staleTime: 60_000 }).then(() => {
        const it = qc.getQueryData<Item>(['item', id])
        if (it?.has_thumb) new Image().src = displaySrc(it)
      })
    }
  }, [next.data, qc])

  const goNext = useCallback(() => {
    if (!item) return
    setTrail((t) => [...t, item.id])
    setCursor(item.id)
    setPinned(null)
  }, [item])

  const goPrev = useCallback(async () => {
    const prevId = trail.at(-1)
    if (!prevId) return
    setTrail((t) => t.slice(0, -1))
    try {
      const it = await api.getItem(prevId)
      qc.setQueryData(['item', it.id], it)
      setPinned(it)
    } catch (e) {
      notify({ key: 'triage', tone: 'error', message: `Couldn't go back: ${describeError(e)}` })
    }
  }, [trail, qc, notify])

  const record = (batch: string | null, it: Item) => {
    if (!batch) return false
    setBatches((b) => [...b.slice(-49), { batch, itemId: it.id }])
    force((n) => n + 1)
    return true
  }

  const undo = async () => {
    const last = batches.at(-1)
    if (!last) return
    setBatches((b) => b.slice(0, -1))
    try {
      await api.undoBatch(last.batch)
      afterUndo()
      const it = await api.getItem(last.itemId)
      qc.setQueryData(['item', it.id], it)
      if (current?.id !== it.id) {
        setTrail((t) => t.filter((x) => x !== it.id))
        setPinned(it)
      } else force((n) => n + 1)
      // still decided if an earlier change on it stands
      if (!batches.slice(0, -1).some((b) => b.itemId === it.id)) setDecided((d) => d.filter((x) => x !== it.id))
      refreshLeft()
      setSaid(`Undone on ${it.filename}`)
    } catch (e) {
      notify({ key: 'triage', tone: 'error', message: `Couldn't undo: ${describeError(e)}` })
    }
  }

  const act = async (a: CullAction) => {
    if (a.kind === 'undo') return void undo()
    if (a.kind === 'next') return goNext()
    if (a.kind === 'prev') return void goPrev()
    if (!item) return
    if (a.kind === 'tag') return setTagOpen(true)
    if (a.kind === 'folder') return setFolderOpen(true)
    let batch: string | null = null
    if (a.kind === 'flag') batch = await patch(item, { flag: a.flag })
    else if (a.kind === 'rate') batch = await patch(item, { rating: a.rating })
    else if (a.kind === 'label') batch = await patch(item, { label: item.label === a.label ? null : a.label })
    else if (a.kind === 'trash') batch = await file.trash(item)
    if (!record(batch, item)) return
    markDone(item.id)
    refreshLeft()
    const fresh = qc.getQueryData<Item>(['item', item.id]) ?? item
    setSaid(`${fresh.filename}: ${cullText(fresh) || 'unflagged'}${fresh.rating ? `, ${fresh.rating} stars` : ''}`)
    if ((auto && advances(a)) || a.kind === 'trash') goNext()
  }

  const fileTo = async (kind: 'tags' | 'folder', value: string[] | string) => {
    if (!item) return
    const batch = kind === 'tags' ? await file.addTags(item, value as string[]) : await file.addFolder(item, value as string)
    if (!record(batch, item)) return
    markDone(item.id)
    refreshLeft()
    setSaid(kind === 'tags' ? `Tagged ${(value as string[]).map((t) => `#${t}`).join(' ')}` : `Filed in ${folders?.find((f) => f.id === value)?.name ?? 'the folder'}`)
    if (auto && kind === 'folder') goNext()
  }

  // triage is a full screen: its keys win over the library's
  useLayer('preview', true)
  useShortcut('preview', (e) => {
    if (e.key === 'Escape') {
      exit()
      return true
    }
    const a = triageKey(e)
    if (!a) return false
    e.preventDefault()
    void act(a)
    return true
  })

  const exit = () => setQuery(view === 'unfiled' ? { view: 'unfiled', deleted: false } : {})

  // phone: swipe on the image
  const touch = useRef<{ x: number; y: number } | null>(null)
  const remaining = left.data?.remaining ?? next.data?.remaining ?? 0
  const total = view === 'unfiled' ? done + remaining : remaining
  const position = view === 'unfiled' ? done : trail.length + (item ? 1 : 0)
  const pct = total ? Math.round((position / total) * 100) : 100
  const titleId = useId()

  return (
    <div className={`triage-screen${mobile ? ' mobile' : ''}`} role="main" aria-labelledby={titleId}>
      <div className="triage-bar">
        <button className="btn-icon" onClick={exit} aria-label={mobile ? "Leave triage" : undefined} title="Back to the library (Esc)">
          <X size={16} aria-hidden="true" />
          {!mobile && 'Done triaging'}
        </button>
        <h1 id={titleId} className="triage-title view-title">
          Triage · {view === 'unfiled' ? 'Unfiled' : 'All items'}
        </h1>
        <div className="triage-progress">
          <progress max={100} value={pct} aria-label="Progress" aria-valuetext={`${position} of ${total}`} />
          <span>
            {view === 'unfiled' ? `${remaining} left` : `${position} of ${total}`} · {done} done
          </span>
        </div>
        <label className="check-inline">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Auto-advance
        </label>
        {!mobile && (
          <details className="triage-keys">
            <summary>Keys</summary>
            <dl>
              <dt>P / X / U</dt>
              <dd>pick, reject, unflag</dd>
              <dt>0–5</dt>
              <dd>rating</dd>
              <dt>6–9</dt>
              <dd>red, orange, yellow, green label</dd>
              <dt>T / F</dt>
              <dd>tag, folder</dd>
              <dt>← →</dt>
              <dd>previous, next</dd>
              <dt>⌘Z</dt>
              <dd>undo the last change</dd>
              <dt>Delete</dt>
              <dd>move to Trash</dd>
            </dl>
          </details>
        )}
      </div>

      <span className="sr-only" role="status" aria-live="polite">
        {said}
      </span>

      {!item && !next.isLoading && (
        <div className="triage-empty">
          <h2>{view === 'unfiled' ? 'Nothing left to decide in Unfiled.' : 'That’s everything.'}</h2>
          <p>
            {done ? `${done} ${done === 1 ? 'item' : 'items'} decided this session.` : 'Nothing to triage here.'}
            {view === 'unfiled' && ' Picked, rated and labelled items stay in Unfiled until you tag them or file them in a folder.'}
          </p>
          <button className="btn-primary" onClick={exit}>
            Back to the library
          </button>
        </div>
      )}
      {next.isLoading && !item && <p className="center">Loading…</p>}

      {item && (
        <div className="triage-body">
          <div
            className="triage-stage"
            onTouchStart={(e) => {
              const t = e.touches[0]
              touch.current = e.touches.length === 1 ? { x: t.clientX, y: t.clientY } : null
            }}
            onTouchEnd={(e) => {
              const s = touch.current
              touch.current = null
              if (!s) return
              const t = e.changedTouches[0]
              const a = swipeAction(swipeDirection(t.clientX - s.x, t.clientY - s.y))
              if (a) void act(a)
            }}
          >
            <TriageMedia key={item.id} item={item} />
            {item.flag && <span className={`triage-stamp ${item.flag}`}>{item.flag === 'pick' ? 'Picked' : 'Rejected'}</span>}
          </div>
          <aside className="triage-side" aria-label="Decide">
            <h2 className="triage-fname" title={item.filename}>
              {item.filename}
            </h2>
            <p className="triage-meta">
              {[item.width && item.height ? `${item.width}×${item.height}` : '', item.ext.toUpperCase(), new Date(item.created_at).toLocaleDateString()].filter(Boolean).join(' · ')}
            </p>
            <FlagButtons flag={item.flag} keys size="large" onFlag={(flag) => void act({ kind: 'flag', flag })} />
            <div className="stars triage-stars" role="group" aria-label={`Rating: ${item.rating || 'none'}`}>
              {[1, 2, 3, 4, 5].map((s) => (
                <button
                  key={s}
                  className={s <= item.rating ? 'on' : ''}
                  aria-label={`${s} star${s > 1 ? 's' : ''}`}
                  aria-pressed={s <= item.rating}
                  aria-keyshortcuts={String(s)}
                  onClick={() => void act({ kind: 'rate', rating: s === item.rating ? 0 : s })}
                >
                  <Star size={20} fill={s <= item.rating ? 'currentColor' : 'none'} aria-hidden="true" />
                </button>
              ))}
            </div>
            <LabelPicker label={item.label} keys onLabel={(label) => void (label ? act({ kind: 'label', label }) : patch(item, { label: null }).then((b) => record(b, item)))} />
            <div className="triage-chips">
              {item.tags.map((t) => (
                <span key={t} className="chip">
                  #{t}
                </span>
              ))}
              {item.folder_ids.map((f) => (
                <span key={f} className="chip folder-chip">
                  {folders?.find((x) => x.id === f)?.name ?? f}
                </span>
              ))}
            </div>
            <div className="triage-actions">
              <button className="btn-icon" onClick={() => setTagOpen(true)} aria-keyshortcuts="T">
                <Hash size={14} aria-hidden="true" /> Tag <kbd aria-hidden="true">T</kbd>
              </button>
              <button className="btn-icon" onClick={() => setFolderOpen(true)} aria-keyshortcuts="F">
                <Folder size={14} aria-hidden="true" /> Folder <kbd aria-hidden="true">F</kbd>
              </button>
              <button className="btn-icon danger" onClick={() => void act({ kind: 'trash' })} aria-keyshortcuts="Delete">
                <Trash2 size={14} aria-hidden="true" /> Trash
              </button>
            </div>
            <div className="triage-nav">
              <button className="btn-icon" onClick={() => void goPrev()} disabled={!trail.length} aria-keyshortcuts="ArrowLeft">
                <ArrowLeft size={16} aria-hidden="true" /> Previous
              </button>
              <button className="btn-icon" onClick={() => void undo()} disabled={!batches.length} aria-keyshortcuts="Meta+Z Control+Z" title="Undo the last change (⌘Z)">
                <Undo2 size={16} aria-hidden="true" /> Undo
              </button>
              <button className="btn-icon btn-primary" onClick={goNext} aria-keyshortcuts="ArrowRight">
                Next <ArrowRight size={16} aria-hidden="true" />
              </button>
            </div>
            {mobile && <p className="hint">Swipe up to pick, down to reject, sideways to move.</p>}
          </aside>
        </div>
      )}

      {tagOpen && item && (
        <TagBox
          onClose={() => setTagOpen(false)}
          onAdd={(tags) => {
            setTagOpen(false)
            void fileTo('tags', tags)
          }}
        />
      )}
      {folderOpen && item && (
        <FolderPicker
          exclude={item.folder_ids}
          onClose={() => setFolderOpen(false)}
          onPick={(id) => {
            setFolderOpen(false)
            void fileTo('folder', id)
          }}
        />
      )}
    </div>
  )
}

function TriageMedia({ item }: { item: Item }) {
  const [src, setSrc] = useState(item.has_thumb ? `/api/items/${item.id}/thumb?size=1024&v=${item.preview_rev}` : '')
  useEffect(() => {
    if (!item.has_thumb) return
    const want = displaySrc(item)
    const img = new Image()
    img.onload = () => setSrc(want)
    img.src = want
    return () => {
      img.onload = null
    }
  }, [item])
  if (item.kind === 'video') return <video src={`/api/items/${item.id}/file`} controls autoPlay muted loop playsInline aria-label={item.filename} />
  if (!src) return <div className="preview-fallback">No preview for .{item.ext}</div>
  return <img src={src} alt={item.filename} draggable={false} />
}

/** T: tags, comma-separated, with suggestions from the library's tags. */
function TagBox({ onAdd, onClose }: { onAdd: (tags: string[]) => void; onClose: () => void }) {
  const { data: tags } = useTags()
  const [text, setText] = useState('')
  const [hi, setHi] = useState(0)
  const id = useId()
  const last = text.split(',').at(-1)?.trim() ?? ''
  const sugg = useMemo(
    () =>
      last
        ? (tags ?? [])
            .map((t) => ({ t, s: matchScore(last, t.name) }))
            .filter((x) => x.s !== null && x.s < 5)
            .sort((a, b) => (a.s as number) - (b.s as number) || b.t.count - a.t.count)
            .slice(0, 8)
            .map((x) => x.t.name)
        : [],
    [last, tags],
  )
  const complete = (name: string) => {
    const parts = text.split(',').slice(0, -1).map((p) => p.trim()).filter(Boolean)
    setText([...parts, name].join(', ') + ', ')
  }
  const submit = () => {
    const list = text.split(',').map((t) => t.trim()).filter(Boolean)
    if (list.length) onAdd(list)
  }
  return (
    <Modal title="Add tags" onClose={onClose} className="dialog">
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <label htmlFor={id}>Tags (comma-separated)</label>
        <input
          id={id}
          data-autofocus
          role="combobox"
          aria-expanded={sugg.length > 0}
          aria-controls={`${id}-list`}
          aria-activedescendant={sugg.length ? `${id}-o${hi}` : undefined}
          value={text}
          autoComplete="off"
          onChange={(e) => {
            setText(e.target.value)
            setHi(0)
          }}
          onKeyDown={(e) => {
            if (!sugg.length) return
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setHi((h) => (h + 1) % sugg.length)
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setHi((h) => (h - 1 + sugg.length) % sugg.length)
            } else if (e.key === 'Tab') {
              e.preventDefault()
              complete(sugg[hi])
            }
          }}
        />
        <div id={`${id}-list`} role="listbox" aria-label="Matching tags" className="tag-sugg">
          {sugg.map((s, i) => (
            <div key={s} id={`${id}-o${i}`} role="option" aria-selected={i === hi} className={`ac-option${i === hi ? ' active' : ''}`} onMouseDown={(e) => e.preventDefault()} onClick={() => complete(s)}>
              #{s}
            </div>
          ))}
        </div>
        <p className="hint">Tab completes the highlighted tag; Enter adds them all.</p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!text.trim()}>
            Add tags
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** F: a quick folder picker; 1-9 pick the first nine shown. */
export function FolderPicker({ onPick, onClose, exclude = [] }: { onPick: (id: string) => void; onClose: () => void; exclude?: string[] }) {
  const { data: folders } = useFolders()
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const id = useId()
  const list = useMemo(
    () =>
      (folders ?? [])
        .filter((f) => !exclude.includes(f.id))
        .map((f) => ({ f, s: matchScore(q, f.name) }))
        .filter((x) => x.s !== null)
        .sort((a, b) => (a.s as number) - (b.s as number) || a.f.name.localeCompare(b.f.name))
        .map((x) => x.f),
    [folders, q, exclude],
  )
  const cur = Math.min(hi, list.length - 1)
  return (
    <Modal title="Add to folder" onClose={onClose} className="dialog folder-picker">
      <input
        data-autofocus
        role="combobox"
        aria-label="Find a folder"
        aria-expanded={list.length > 0}
        aria-controls={`${id}-list`}
        aria-activedescendant={list.length ? `${id}-o${cur}` : undefined}
        placeholder="Type to find a folder…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value)
          setHi(0)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setHi((h) => Math.min(h + 1, list.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setHi((h) => Math.max(h - 1, 0))
          } else if (e.key === 'Enter' && list[cur]) {
            e.preventDefault()
            onPick(list[cur].id)
          } else if (/^[1-9]$/.test(e.key) && !q && list[Number(e.key) - 1]) {
            e.preventDefault()
            onPick(list[Number(e.key) - 1].id)
          }
        }}
      />
      <div id={`${id}-list`} role="listbox" aria-label="Folders" className="folder-pick-list">
        {list.map((f, i) => (
          <div
            key={f.id}
            id={`${id}-o${i}`}
            role="option"
            aria-selected={i === cur}
            className={`ac-option${i === cur ? ' active' : ''}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(f.id)}
          >
            {!q && i < 9 && <kbd aria-hidden="true">{i + 1}</kbd>} {f.name}
          </div>
        ))}
        {!list.length && <p className="hint">{folders?.length ? 'No folder matches.' : 'No folders yet: make one in the sidebar.'}</p>}
      </div>
    </Modal>
  )
}
