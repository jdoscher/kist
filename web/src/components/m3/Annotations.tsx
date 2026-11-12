// Notes pinned to the image in the full-screen preview: drop a pin or drag
// a box, type a note; on video, mark a moment or a range on the scrubber
// and see the notes for "now". A side list (resolve, edit, delete) is the
// keyboard and screen-reader way to all of them. Z toggles a 100% loupe.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Crosshair, MapPin, MessageSquare, Pencil, SquareDashed, Trash2, X } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Item } from '../../api'
import {
  activeAt,
  annotationWhere,
  boxFrom,
  fmtTime,
  rangeOnBar,
  sortAnnotations,
  toFraction,
  type Annotation,
  type AnnotationIn,
} from '../../annotate'
import { api3 } from '../../creativeApi'
import { useCaps } from '../../data'
import { useShortcut } from '../../useShortcuts'
import { useWrite } from '../../useWrite'
import { Loupe } from './QuickLook'

type Tool = 'point' | 'box' | null

const PANEL_KEY = 'kist.annotationsOpen'

export function Annotated({ item, children }: { item: Item; children: ReactNode }) {
  const caps = useCaps()
  const qc = useQueryClient()
  const w = useWrite()
  const uid = useId()
  const isVideo = item.kind === 'video'
  const isImage = item.kind === 'image' && item.has_thumb
  const supported = isVideo || isImage
  const [open, setOpenRaw] = useState(() => {
    try {
      return localStorage.getItem(PANEL_KEY) === '1'
    } catch {
      return false
    }
  })
  const setOpen = (v: boolean) => {
    setOpenRaw(v)
    try {
      localStorage.setItem(PANEL_KEY, v ? '1' : '0')
    } catch {
      /* private mode */
    }
  }
  const [tool, setTool] = useState<Tool>(null)
  const [loupe, setLoupe] = useState(false)
  const [draft, setDraft] = useState<AnnotationIn | null>(null)
  const [drag, setDrag] = useState<{ a: { x: number; y: number }; b: { x: number; y: number } } | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [showResolved, setShowResolved] = useState(true)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(item.duration_s ?? 0)
  const [markIn, setMarkIn] = useState<number | null>(null)
  const layer = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)

  const list = useQuery({
    queryKey: ['annotations', item.id],
    queryFn: () => api3.annotations(item.id),
    enabled: supported,
    staleTime: 10_000,
  })
  const all = useMemo(() => sortAnnotations(list.data ?? []), [list.data])
  const shown = showResolved ? all : all.filter((a) => !a.resolved)
  const numberOf = (a: Annotation) => all.indexOf(a) + 1
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['annotations', item.id] })
    void qc.invalidateQueries({ queryKey: ['history'] })
    void qc.invalidateQueries({ queryKey: ['batches'] })
  }

  // the video element, for time
  const video = () => wrap.current?.querySelector('video') ?? null
  useEffect(() => {
    const v = video()
    if (!v) return
    const onTime = () => setTime(v.currentTime)
    const onMeta = () => Number.isFinite(v.duration) && setDuration(v.duration)
    v.addEventListener('timeupdate', onTime)
    v.addEventListener('loadedmetadata', onMeta)
    onMeta()
    return () => {
      v.removeEventListener('timeupdate', onTime)
      v.removeEventListener('loadedmetadata', onMeta)
    }
  }, [item.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const seek = (t: number | null) => {
    const v = video()
    if (v && t != null) {
      v.currentTime = t
      setTime(t)
    }
  }

  const save = (a: AnnotationIn) =>
    w.run('add', () => api3.addAnnotation(item.id, a), {
      what: 'save the note',
      onDone: (r) => {
        setDraft(null)
        setFocusId(r.id)
        refresh()
      },
    })
  const update = (a: Annotation, change: Partial<AnnotationIn>) =>
    w.run(`a:${a.id}`, () => api3.patchAnnotation(item.id, a.id, change), { what: 'save the note', onDone: refresh })
  const remove = (a: Annotation) =>
    w.run(`a:${a.id}`, () => api3.deleteAnnotation(item.id, a.id), { what: 'delete the note', onDone: refresh })

  const startNoteAtTime = (t0: number, t1: number | null = null) => {
    setOpen(true)
    setDraft({ kind: 'range', t_start: t0, t_end: t1, text: '' })
  }

  useShortcut(
    'preview',
    (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return false
      const k = e.key.toLowerCase()
      if (k === 'a') setOpen(!open)
      else if (k === 'z' && isImage) setLoupe(!loupe)
      else if (k === 'escape' && (draft || tool || loupe)) {
        setDraft(null)
        setTool(null)
        setLoupe(false)
      } else if (caps.canEdit && isVideo && k === 'm') startNoteAtTime(time)
      else if (caps.canEdit && isVideo && k === 'i') setMarkIn(time)
      else if (caps.canEdit && isVideo && k === 'o' && markIn != null) {
        startNoteAtTime(Math.min(markIn, time), Math.max(markIn, time))
        setMarkIn(null)
      } else return false
      e.preventDefault()
      return true
    },
    supported,
  )

  if (!supported) return <>{children}</>

  const frac = (e: React.PointerEvent) => toFraction(layer.current!.getBoundingClientRect(), e.clientX, e.clientY)
  const drawing = !!tool && caps.canEdit && !draft

  const marks = isImage
    ? shown.filter((a) => a.kind !== 'range')
    : []
  const now = isVideo ? activeAt(all, time) : []

  return (
    <div className={`annotated${open ? ' panel-open' : ''}`}>
      <div ref={wrap} className={`ann-media${loupe && !tool ? " loupe-on" : ""}`}>
        {isImage ? <Loupe item={item} on={loupe}>{children}</Loupe> : children}
        {isImage && (
          <div
            ref={layer}
            className={`ann-layer${drawing ? ' drawing' : ''}`}
            onPointerDown={(e) => {
              if (!drawing) return
              e.currentTarget.setPointerCapture(e.pointerId)
              const p = frac(e)
              setDrag({ a: p, b: p })
            }}
            onPointerMove={(e) => drag && setDrag({ ...drag, b: frac(e) })}
            onPointerUp={() => {
              if (!drag) return
              const box = tool === 'box' ? boxFrom(drag.a, drag.b) : null
              setDrag(null)
              setOpen(true)
              setDraft(box ? { kind: 'box', ...box, text: '' } : { kind: 'point', x: drag.b.x, y: drag.b.y, text: '' })
            }}
          >
            {marks.map((a) => (
              <button
                key={a.id}
                className={`ann-mark ${a.kind}${a.resolved ? ' resolved' : ''}${focusId === a.id ? ' focus' : ''}`}
                style={
                  a.kind === 'box'
                    ? { left: `${(a.x ?? 0) * 100}%`, top: `${(a.y ?? 0) * 100}%`, width: `${(a.w ?? 0) * 100}%`, height: `${(a.h ?? 0) * 100}%` }
                    : { left: `${(a.x ?? 0) * 100}%`, top: `${(a.y ?? 0) * 100}%` }
                }
                aria-label={`Note ${numberOf(a)}: ${a.text || 'no text'}${a.resolved ? ' (resolved)' : ''}`}
                title={a.text}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => {
                  setFocusId(a.id)
                  setOpen(true)
                }}
              >
                <span className="ann-num">{numberOf(a)}</span>
              </button>
            ))}
            {drag && tool === 'box' && (() => {
              const b = boxFrom(drag.a, drag.b, 0)
              return b ? <span className="ann-mark box drafting" style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` }} /> : null
            })()}
            {draft && draft.kind !== 'range' && (
              <span
                className={`ann-mark ${draft.kind} drafting`}
                style={
                  draft.kind === 'box'
                    ? { left: `${(draft.x ?? 0) * 100}%`, top: `${(draft.y ?? 0) * 100}%`, width: `${(draft.w ?? 0) * 100}%`, height: `${(draft.h ?? 0) * 100}%` }
                    : { left: `${(draft.x ?? 0) * 100}%`, top: `${(draft.y ?? 0) * 100}%` }
                }
              />
            )}
          </div>
        )}
        {isVideo && duration > 0 && (
          <div className="ann-scrub" role="group" aria-label="Notes on the timeline">
            {all
              .filter((a) => a.t_start != null && (showResolved || !a.resolved))
              .map((a) => (
                <button
                  key={a.id}
                  className={`ann-range${a.resolved ? ' resolved' : ''}${now.includes(a) ? ' now' : ''}`}
                  style={rangeOnBar(a.t_start ?? 0, a.t_end, duration)}
                  aria-label={`Note ${numberOf(a)} ${annotationWhere(a)}: ${a.text || 'no text'}. Jump there`}
                  title={`${annotationWhere(a)} ${a.text}`}
                  onClick={() => {
                    seek(a.t_start)
                    setFocusId(a.id)
                  }}
                />
              ))}
            {markIn != null && <span className="ann-range in" style={rangeOnBar(markIn, time, duration)} aria-hidden="true" />}
            <span className="ann-playhead" style={{ left: `${(time / duration) * 100}%` }} aria-hidden="true" />
          </div>
        )}
      </div>

      <button
        className={`ann-toggle btn-icon${open ? ' on' : ''}`}
        aria-pressed={open}
        aria-controls={open ? `${uid}-panel` : undefined}
        onClick={() => setOpen(!open)}
        title="Notes on the image (A)"
        aria-keyshortcuts="A"
      >
        <MessageSquare size={16} aria-hidden="true" />
        Notes{all.length ? ` (${all.filter((a) => !a.resolved).length})` : ''}
      </button>
      {isImage && (
        <button className={`ann-loupe-toggle btn-icon${loupe ? ' on' : ''}`} aria-pressed={loupe} onClick={() => setLoupe(!loupe)} title="100% loupe (Z)" aria-keyshortcuts="Z">
          <Crosshair size={16} aria-hidden="true" />
          Loupe
        </button>
      )}

      {open && (
        <aside id={`${uid}-panel`} className="ann-panel" aria-label="Notes">
          <div className="ann-panel-head">
            <h2>Notes</h2>
            <button className="icon-btn" aria-label="Close notes" onClick={() => setOpen(false)}>
              <X size={16} aria-hidden="true" />
            </button>
          </div>
          {caps.canEdit && isImage && (
            <div className="ann-tools" role="group" aria-label="Add a note">
              <button className={`btn-icon${tool === 'point' ? ' on' : ''}`} aria-pressed={tool === 'point'} onClick={() => setTool(tool === 'point' ? null : 'point')}>
                <MapPin size={14} aria-hidden="true" /> Pin
              </button>
              <button className={`btn-icon${tool === 'box' ? ' on' : ''}`} aria-pressed={tool === 'box'} onClick={() => setTool(tool === 'box' ? null : 'box')}>
                <SquareDashed size={14} aria-hidden="true" /> Box
              </button>
              <button
                className="btn-icon"
                onClick={() => setDraft({ kind: 'point', x: 0.5, y: 0.5, text: '' })}
                title="A note pinned to the middle; for keyboard use"
              >
                Note on the whole image
              </button>
            </div>
          )}
          {caps.canEdit && isImage && tool && !draft && (
            <p className="hint" role="status">
              {tool === 'point' ? 'Click the image where the note belongs.' : 'Drag over the part of the image the note is about.'}
            </p>
          )}
          {caps.canEdit && isVideo && (
            <div className="ann-tools" role="group" aria-label="Add a note">
              <button className="btn-icon" onClick={() => startNoteAtTime(time)} aria-keyshortcuts="M">
                <MapPin size={14} aria-hidden="true" /> Note at {fmtTime(time)}
              </button>
              {markIn == null ? (
                <button onClick={() => setMarkIn(time)} aria-keyshortcuts="I">
                  Start a range here (I)
                </button>
              ) : (
                <button
                  onClick={() => {
                    startNoteAtTime(Math.min(markIn, time), Math.max(markIn, time))
                    setMarkIn(null)
                  }}
                  aria-keyshortcuts="O"
                >
                  End range {fmtTime(markIn)}–{fmtTime(time)} (O)
                </button>
              )}
            </div>
          )}
          {draft && (
            <DraftForm
              where={draft.kind === 'range' ? annotationWhere({ ...draft, t_start: draft.t_start ?? 0, t_end: draft.t_end ?? null } as Annotation) : draft.kind === 'box' ? 'Box' : 'Pin'}
              busy={w.busy('add')}
              onCancel={() => setDraft(null)}
              onSave={(text) => void save({ ...draft, text })}
            />
          )}
          {isVideo && now.length > 0 && (
            <div className="ann-now" aria-live="polite">
              <h3 className="section-label">Now ({fmtTime(time)})</h3>
              {now.map((a) => (
                <p key={a.id}>{a.text}</p>
              ))}
            </div>
          )}
          <label className="check-inline">
            <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> Show resolved
          </label>
          {shown.length === 0 && !draft && <p className="hint">No notes yet.{caps.canEdit ? (isVideo ? ' Press M to note this moment.' : ' Choose Pin or Box, then click the image.') : ''}</p>}
          <ol className="ann-list">
            {shown.map((a) => (
              <AnnotationRow
                key={a.id}
                a={a}
                n={numberOf(a)}
                focus={focusId === a.id}
                editable={caps.canEdit}
                busy={w.busy(`a:${a.id}`)}
                onFocus={() => {
                  setFocusId(a.id)
                  if (a.t_start != null) seek(a.t_start)
                }}
                onResolve={(resolved) => void update(a, { resolved })}
                onText={(text) => void update(a, { text })}
                onDelete={() => void remove(a)}
              />
            ))}
          </ol>
          {list.isError && <p className="error">Couldn’t load the notes.</p>}
        </aside>
      )}
    </div>
  )
}

function DraftForm({ where, busy, onSave, onCancel }: { where: string; busy: boolean; onSave: (t: string) => void; onCancel: () => void }) {
  const [text, setText] = useState('')
  const id = useId()
  return (
    <form
      className="ann-draft"
      onSubmit={(e) => {
        e.preventDefault()
        onSave(text.trim())
      }}
    >
      <label htmlFor={id}>New note · {where}</label>
      <textarea
        id={id}
        autoFocus
        rows={3}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSave(text.trim())
          if (e.key === 'Escape') {
            e.stopPropagation()
            onCancel()
          }
        }}
        placeholder="What about this part? (⌘Enter saves)"
      />
      <div className="dialog-actions">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={busy}>
          Save note
        </button>
      </div>
    </form>
  )
}

function AnnotationRow({
  a,
  n,
  focus,
  editable,
  busy,
  onFocus,
  onResolve,
  onText,
  onDelete,
}: {
  a: Annotation
  n: number
  focus: boolean
  editable: boolean
  busy: boolean
  onFocus: () => void
  onResolve: (v: boolean) => void
  onText: (t: string) => void
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(a.text)
  const ref = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (focus) ref.current?.scrollIntoView({ block: 'nearest' })
  }, [focus])
  return (
    <li ref={ref} className={`ann-row${focus ? ' focus' : ''}${a.resolved ? ' resolved' : ''}`}>
      <button className="ann-row-main" onClick={onFocus} aria-describedby={`${a.id}-text`}>
        <span className="sr-only">Note </span>
        <span className="ann-num">{n}</span>
        <span className="sr-only">, </span>
        <span className="ann-where">{annotationWhere(a)}</span>
      </button>
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            onText(text.trim())
            setEditing(false)
          }}
        >
          <textarea aria-label={`Text of note ${n}`} autoFocus rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="dialog-actions">
            <button type="button" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              Save
            </button>
          </div>
        </form>
      ) : (
        <p className="ann-text" id={`${a.id}-text`}>{a.text || <em>No text</em>}</p>
      )}
      <div className="ann-row-meta">
        <span>
          {a.author} · {new Date(a.created_at).toLocaleDateString()}
        </span>
        {editable && (
          <>
            <label className="check-inline">
              <input type="checkbox" checked={a.resolved} disabled={busy} onChange={(e) => onResolve(e.target.checked)} />
              Resolved
            </label>
            <button className="icon-btn" aria-label={`Edit note ${n}`} title="Edit" onClick={() => setEditing(true)}>
              <Pencil size={14} aria-hidden="true" />
            </button>
            <button className="icon-btn" aria-label={`Delete note ${n}`} title="Delete" disabled={busy} onClick={onDelete}>
              <Trash2 size={14} aria-hidden="true" />
            </button>
          </>
        )}
        {!editable && a.resolved && (
          <span>
            <Check size={14} aria-hidden="true" /> resolved
          </span>
        )}
      </div>
    </li>
  )
}
