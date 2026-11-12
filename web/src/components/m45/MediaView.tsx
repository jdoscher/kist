// What the full-screen preview shows for Milestone 5 kinds: video (a player
// that plays anything, cover, frames, clips), audio (waveform player),
// SVG (Copy SVG, its colours), fonts (specimen, axes, glyphs), bundles
// (what's inside), design files (their embedded preview) and 3D models
// (model-viewer, loaded only when a model is opened).

import { useQuery } from '@tanstack/react-query'
import { Camera, ChevronLeft, ChevronRight, Copy, Download, Film, ImageIcon, Scissors } from 'lucide-react'
import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState } from 'react'
import { fileUrl, thumbUrl, type Item } from '../../api'
import { useApplyItems, useCaps, useRefresh } from '../../data'
import { fmtBytes } from '../../format'
import { api45, bundleFileUrl, streamUrl } from '../../m45/api45'
import { fmtTime, playableOf, svgColors, waveformBars } from '../../m45/media'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { useNotices } from '../Notices'
import { useM45 } from './M45'

const ModelView = lazy(() => import('./ModelView'))

function useToolError() {
  const { notify } = useNotices()
  return (what: string, e: unknown) => notify({ message: `Couldn’t ${what}: ${describeError(e)}` })
}

// ---------- video ----------

function VideoPlayer({ item }: { item: Item }) {
  const ref = useRef<HTMLVideoElement>(null)
  const playable = playableOf(item.media)
  const caps = useCaps()
  const apply = useApplyItems()
  const refresh = useRefresh()
  const { notify } = useNotices()
  const fail = useToolError()
  const { openClip } = useM45()
  const { selectOnly, setPreviewId } = useAppState()
  const [busy, setBusy] = useState('')
  // a file the browser can't play gets a web-safe copy, made on first play
  const state = useQuery({
    queryKey: ['stream', item.id],
    queryFn: () => api45.streamState(item.id),
    enabled: playable !== 'native',
    refetchInterval: (q) => (q.state.data === 'preparing' ? 2000 : false),
  })
  const ready = playable === 'native' || state.data === 'ready'
  const t = () => ref.current?.currentTime ?? 0

  const cover = async () => {
    setBusy('cover')
    try {
      apply([await api45.setCover(item.id, t())])
      notify({ message: `Cover set to ${fmtTime(t())}.` })
    } catch (e) {
      fail('set the cover', e)
    } finally {
      setBusy('')
    }
  }
  const frame = async () => {
    setBusy('frame')
    try {
      const at = t()
      const r = await api45.saveFrame(item.id, at)
      refresh.itemsAdded()
      notify({
        message: `Saved the frame at ${fmtTime(at)} as ${r.item.filename}.`,
        action: {
          label: 'Open',
          run: () => {
            selectOnly(r.item.id)
            setPreviewId(r.item.id)
          },
        },
      })
    } catch (e) {
      fail('save the frame', e)
    } finally {
      setBusy('')
    }
  }
  const clip = () => {
    const now = t()
    const dur = item.duration_s ?? ref.current?.duration ?? now + 5
    openClip({ item, start: now, end: Math.min(dur, now + 5), player: () => ref.current })
  }

  return (
    <div className="media-stage">
      {ready ? (
        <video ref={ref} src={streamUrl(item)} controls autoPlay playsInline preload="metadata" poster={thumbUrl(item, 1024) ?? undefined} />
      ) : state.data === 'none' || playable === 'none' ? (
        <div className="preview-fallback">
          <p>Kist can’t play this file. Download the original to open it in a video app.</p>
        </div>
      ) : (
        <div className="preview-fallback" role="status">
          <p>Making a copy your browser can play… (the first time only)</p>
          <progress aria-label="Preparing the video" />
        </div>
      )}
      {ready && (
        <div className="media-tools" role="toolbar" aria-label="Video tools">
          {caps.canEdit && (
            <button type="button" className="btn-icon" disabled={!!busy} onClick={() => void cover()} title="Use the frame on screen as the thumbnail">
              <ImageIcon size={14} aria-hidden="true" /> Set cover
            </button>
          )}
          {caps.canEdit && (
            <button type="button" className="btn-icon" disabled={!!busy} onClick={() => void frame()} title="Save the frame on screen as a new picture">
              <Camera size={14} aria-hidden="true" /> Save frame
            </button>
          )}
          {caps.canEdit && (
            <button type="button" className="btn-icon" onClick={clip} title="Cut a clip, GIF or WebP from this video">
              <Scissors size={14} aria-hidden="true" /> Clip…
            </button>
          )}
          {playable === 'proxy' && <span className="hint">Playing a web copy; the original is unchanged.</span>}
        </div>
      )}
    </div>
  )
}

// ---------- audio ----------

function AudioPlayer({ item }: { item: Item }) {
  const ref = useRef<HTMLAudioElement>(null)
  const wave = useQuery({ queryKey: ['waveform', item.id], queryFn: () => api45.waveform(item.id), retry: false })
  const [pos, setPos] = useState(0)
  const caps = useCaps()
  const { openClip } = useM45()
  const bars = useMemo(() => (wave.data ? waveformBars(wave.data.data, wave.data.bits, 160) : []), [wave.data])
  const dur = item.duration_s ?? 0
  const audio = (item.media?.audio ?? {}) as { artist?: string; title?: string }
  useEffect(() => {
    const a = ref.current
    if (!a) return
    const on = () => setPos(a.duration ? a.currentTime / a.duration : 0)
    a.addEventListener('timeupdate', on)
    return () => a.removeEventListener('timeupdate', on)
  }, [])
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const a = ref.current
    if (!a || !a.duration) return
    const r = e.currentTarget.getBoundingClientRect()
    a.currentTime = ((e.clientX - r.left) / r.width) * a.duration
  }
  return (
    <div className="media-stage audio-stage">
      <div className="audio-card">
        <div className="audio-title">
          <strong>{audio.title || item.filename}</strong>
          {audio.artist && <span className="hint"> · {audio.artist}</span>}
        </div>
        {/* the bar is a pointer shortcut; the audio controls below seek by keyboard */}
        <div className="waveform" onClick={seek} aria-hidden="true">
          {bars.map((h, i) => (
            <span key={i} className={i / bars.length <= pos ? 'played' : ''} style={{ height: `${Math.max(2, h * 100)}%` }} />
          ))}
          {wave.isError && <span className="hint">No waveform yet.</span>}
        </div>
        <audio ref={ref} src={streamUrl(item)} controls preload="metadata" />
        {caps.canEdit && (
          <div className="media-tools" role="toolbar" aria-label="Audio tools">
            <button
              type="button"
              className="btn-icon"
              onClick={() => {
                const now = ref.current?.currentTime ?? 0
                openClip({ item, start: now, end: Math.min(dur || now + 10, now + 10), player: () => ref.current })
              }}
            >
              <Scissors size={14} aria-hidden="true" /> Clip…
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------- SVG ----------

function SvgView({ item }: { item: Item }) {
  const text = useQuery({ queryKey: ['svg', item.id], queryFn: () => api45.svgText(item.id), retry: false })
  const [copied, setCopied] = useState('')
  const url = useMemo(() => (text.data ? URL.createObjectURL(new Blob([text.data], { type: 'image/svg+xml' })) : null), [text.data])
  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url])
  const colors = svgColors(item.media)
  const copy = (what: string, value: string) =>
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(what)
      setTimeout(() => setCopied(''), 1500)
    })
  return (
    <div className="media-stage">
      <div className="svg-stage">{url ? <img src={url} alt={item.filename} /> : <img src={thumbUrl(item, 1024) ?? fileUrl(item)} alt={item.filename} />}</div>
      <div className="media-tools" role="toolbar" aria-label="SVG tools">
        <button type="button" className="btn-icon" disabled={!text.data} onClick={() => text.data && copy('svg', text.data)}>
          <Copy size={14} aria-hidden="true" /> {copied === 'svg' ? 'Copied' : 'Copy SVG'}
        </button>
        {colors.length > 0 && (
          <ul className="svg-colors" aria-label="Colours in this SVG">
            {colors.map((c) => (
              <li key={c}>
                <button type="button" className="swatch-btn" onClick={() => copy(c, c)} aria-label={`Copy ${c}`} title={`Copy ${c}`}>
                  <span className="swatch" style={{ background: c }} aria-hidden="true" />
                  <span className="mono">{copied === c ? 'Copied' : c}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <span className="sr-only" role="status">
          {copied ? 'Copied to the clipboard' : ''}
        </span>
      </div>
    </div>
  )
}

// ---------- fonts ----------

const SAMPLES = ['The quick brown fox jumps over the lazy dog', 'Sphinx of black quartz, judge my vow', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789']

function FontView({ item }: { item: Item }) {
  const info = useQuery({ queryKey: ['font-info', item.id], queryFn: () => api45.fontInfo(item.id), retry: false })
  const [page, setPage] = useState(0)
  const glyphs = useQuery({ queryKey: ['glyphs', item.id, page], queryFn: () => api45.glyphs(item.id, page), placeholderData: (p) => p })
  const [sample, setSample] = useState(SAMPLES[0])
  const [size, setSize] = useState(56)
  const [axes, setAxes] = useState<Record<string, number>>({})
  const [loaded, setLoaded] = useState<'loading' | 'ok' | 'failed'>('loading')
  const family = `kist-font-${item.id}`
  const id = useId()
  useEffect(() => {
    // the font itself, from /file (same origin, served as font/*)
    const face = new FontFace(family, `url(${fileUrl(item)})`)
    let gone = false
    face
      .load()
      .then((f) => {
        if (gone) return
        document.fonts.add(f)
        setLoaded('ok')
      })
      .catch(() => !gone && setLoaded('failed'))
    return () => {
      gone = true
      document.fonts.delete(face)
    }
  }, [family, item])
  const variation = Object.entries(axes)
    .map(([k, v]) => `"${k}" ${v}`)
    .join(', ')
  const style = { fontFamily: `"${family}", var(--font-ui)`, fontVariationSettings: variation || undefined }
  const fi = info.data
  return (
    <div className="media-stage font-stage">
      <div className="font-head">
        <h2 className="font-name">
          {fi?.family ?? item.filename} {fi?.style && <span className="hint">{fi.style}</span>}
        </h2>
        <p className="hint">
          {fi ? `${fi.glyph_count} glyphs · ${fi.scripts.join(', ') || 'no scripts detected'}${fi.variable ? ' · variable' : ''}${fi.designer ? ` · ${fi.designer}` : ''}` : ''}
          {loaded === 'failed' && ' · Your browser couldn’t load this font for the specimen.'}
        </p>
      </div>
      <label htmlFor={`${id}-sample`} className="sr-only">
        Sample text
      </label>
      <textarea id={`${id}-sample`} className="font-sample" style={{ ...style, fontSize: size }} value={sample} onChange={(e) => setSample(e.target.value)} rows={2} spellCheck={false} />
      <div className="font-controls">
        <label className="range-row">
          Size
          <input type="range" min={12} max={160} value={size} onChange={(e) => setSize(Number(e.target.value))} aria-valuetext={`${size} pixels`} />
          <output className="mono">{size}px</output>
        </label>
        {SAMPLES.map((s, i) => (
          <button key={i} type="button" className="small" onClick={() => setSample(s)} title={`Sample text: ${s}`}>
            {s.slice(0, 12)}…<span className="sr-only"> (sample text)</span>
          </button>
        ))}
        {(fi?.axes ?? []).map((a) => (
          <label key={a.tag} className="range-row">
            {a.name ?? a.tag} <span className="mono dim">{a.tag}</span>
            <input
              type="range"
              min={a.min}
              max={a.max}
              step={(a.max - a.min) / 100 || 1}
              value={axes[a.tag] ?? a.default}
              onChange={(e) => setAxes((x) => ({ ...x, [a.tag]: Number(e.target.value) }))}
            />
            <output className="mono">{Math.round(axes[a.tag] ?? a.default)}</output>
          </label>
        ))}
      </div>
      <section aria-labelledby={`${id}-glyphs`} className="glyph-section">
        <div className="section-head-row">
          <h3 id={`${id}-glyphs`} className="section-label">
            Glyphs {glyphs.data ? `(${glyphs.data.total})` : ''}
          </h3>
          {glyphs.data && glyphs.data.pages > 1 && (
            <span className="pager">
              <button type="button" className="icon-btn" aria-label="Previous glyphs" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                <ChevronLeft size={16} aria-hidden="true" />
              </button>
              <span className="mono">
                {page + 1} / {glyphs.data.pages}
              </span>
              <button type="button" className="icon-btn" aria-label="Next glyphs" disabled={page >= glyphs.data.pages - 1} onClick={() => setPage((p) => p + 1)}>
                <ChevronRight size={16} aria-hidden="true" />
              </button>
            </span>
          )}
        </div>
        <ul className="glyph-grid" style={style}>
          {(glyphs.data?.glyphs ?? []).map((g) => (
            <li key={g.cp} title={`${g.hex}${g.name ? ` ${g.name}` : ''}`}>
              <span className="glyph" aria-hidden="true">
                {g.char}
              </span>
              <span className="glyph-cp mono">{g.hex}</span>
              <span className="sr-only">{g.name ?? g.hex}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

// ---------- bundles ----------

function BundleView({ item }: { item: Item }) {
  const man = useQuery({ queryKey: ['bundle', item.id], queryFn: () => api45.bundle(item.id), retry: false })
  const [chosen, setChosen] = useState<string | null>(null)
  const [model, setModel] = useState(false)
  const entries = man.data?.entries ?? []
  const pick = chosen ?? man.data?.cover ?? null
  const groups = useMemo(() => {
    const g = new Map<string, typeof entries>()
    for (const e of entries) {
      const dir = e.path.includes('/') ? e.path.slice(0, e.path.lastIndexOf('/')) : ''
      if (!g.has(dir)) g.set(dir, [])
      g.get(dir)!.push(e)
    }
    return [...g.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [entries])
  return (
    <div className="media-stage bundle-stage">
      <div className="bundle-view">
        {model && man.data?.model ? (
          <Suspense fallback={<p role="status">Loading the 3D viewer…</p>}>
            <ModelView item={item} src={bundleFileUrl(item.id, man.data.model)} />
          </Suspense>
        ) : pick && entries.find((e) => e.path === pick)?.kind === 'image' ? (
          <img src={bundleFileUrl(item.id, pick)} alt={pick} />
        ) : (
          <img src={thumbUrl(item, 1024) ?? ''} alt={item.filename} />
        )}
      </div>
      <nav className="bundle-tree" aria-label="Files in this bundle">
        <p className="hint">
          {man.data ? `${man.data.count} files, ${fmtBytes(man.data.total_size)}` : man.isError ? describeError(man.error) : 'Loading…'}
        </p>
        {man.data?.model && (
          <button type="button" className="btn-icon small" aria-pressed={model} onClick={() => setModel((m) => !m)}>
            <Film size={14} aria-hidden="true" /> {model ? 'Hide the model' : 'View the model'}
          </button>
        )}
        {groups.map(([dir, list]) => (
          <div key={dir} className="bundle-dir">
            {dir && <div className="bundle-dirname mono">{dir}/</div>}
            <ul>
              {list.map((e) => (
                <li key={e.path} className={pick === e.path ? 'on' : ''}>
                  {e.kind === 'image' ? (
                    <button type="button" className="linkish" onClick={() => { setChosen(e.path); setModel(false) }} aria-pressed={pick === e.path}>
                      {e.path.split('/').pop()}
                    </button>
                  ) : (
                    <span>{e.path.split('/').pop()}</span>
                  )}
                  <span className="hint">{fmtBytes(e.size)}</span>
                  <a href={bundleFileUrl(item.id, e.path)} download={e.path.split('/').pop()} className="icon-btn" aria-label={`Download ${e.path}`} title="Download this file">
                    <Download size={14} aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
    </div>
  )
}

// ---------- design files ----------

function DesignView({ item }: { item: Item }) {
  const thumb = thumbUrl(item, 1024)
  return (
    <div className="media-stage">
      {thumb ? <img src={`/api/items/${item.id}/thumb?size=2048&v=${item.preview_rev}`} alt={item.filename} /> : <div className="preview-fallback"><p>No preview inside this .{item.ext} file.</p></div>}
      <p className="hint design-note">
        Source file (.{item.ext}): Kist shows the preview stored inside it. Open the original in its app to edit it.
      </p>
    </div>
  )
}

export default function MediaView({ item }: { item: Item }) {
  if (item.kind === 'video') return <VideoPlayer item={item} />
  if (item.kind === 'audio') return <AudioPlayer item={item} />
  if (item.kind === 'font') return <FontView item={item} />
  if (item.kind === 'bundle') return <BundleView item={item} />
  if (item.kind === 'design') return <DesignView item={item} />
  if (item.kind === 'model')
    return (
      <Suspense fallback={<p role="status">Loading the 3D viewer…</p>}>
        <ModelView item={item} />
      </Suspense>
    )
  if (item.ext.toLowerCase() === 'svg') return <SvgView item={item} />
  return null
}
