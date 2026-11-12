// First run: the empty library teaches the ways in (drop or paste, Eagle
// and other imports, the browser extension, an AI agent over MCP), and a
// dismissible "Set up Kist" checklist in the sidebar ticks itself off from
// real state.

import { useQuery } from '@tanstack/react-query'
import { Bot, Check, ChevronDown, Copy, Globe, Import, Smartphone, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import { useBatches, useCaps, useStats } from '../../data'
import { mcpCommand, progress, readSetup, saveSetup, setupSteps, type StepId } from '../../onboarding'
import { useAppState } from '../../state'
import { useUploads } from '../UploadManager'

function CopyCode({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="copy-code">
      <pre className="code-block">{text}</pre>
      <button
        className="small btn-icon"
        onClick={() =>
          void navigator.clipboard?.writeText(text).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          })
        }
        aria-label={`Copy ${label}`}
      >
        {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

/** The empty library: four ways to start (two on a phone). */
export function FirstRun() {
  const { mobile, openSettings } = useAppState()
  const caps = useCaps()
  const uploads = useUploads()
  const fileRef = useRef<HTMLInputElement>(null)
  const origin = window.location.origin
  return (
    <section className="first-run" aria-labelledby="first-run-h">
      <h2 id="first-run-h" className="first-run-title">
        Start your kist
      </h2>
      <p className="first-run-lead">Everything you keep stays as plain files on your own disk. Bring things in any of these ways:</p>
      <div className="first-run-cards">
        <article className="fr-card">
          <h3>
            <Upload size={16} aria-hidden="true" /> {mobile ? 'Upload from this phone' : 'Drop or paste'}
          </h3>
          <p>{mobile ? 'Photos, screenshots, files: anything.' : 'Drag files anywhere on this window, or copy an image and press ⌘V (Ctrl+V). Page links bring their preview image.'}</p>
          {caps.canEdit && (
            <>
              <button className="btn-primary btn-icon" onClick={() => fileRef.current?.click()}>
                <Upload size={14} aria-hidden="true" /> Choose files
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
            </>
          )}
        </article>
        {mobile ? (
          <article className="fr-card">
            <h3>
              <Smartphone size={16} aria-hidden="true" /> Add to Home Screen
            </h3>
            <p>In Safari, tap Share, then Add to Home Screen: Kist opens like an app, full screen.</p>
          </article>
        ) : (
          <>
            <article className="fr-card">
              <h3>
                <Import size={16} aria-hidden="true" /> Bring a library over
              </h3>
              <p>
                Moving from Eagle or a folder of files? Drop files into <code>library/inbox/</code> on the server, or import through the
                API: it keeps tags, folders, notes, ratings and dates.
              </p>
              <a href="/docs" target="_blank" rel="noreferrer">
                API docs<span className="sr-only"> (new tab)</span>
              </a>
            </article>
            <article className="fr-card">
              <h3>
                <Globe size={16} aria-hidden="true" /> Save from your browser
              </h3>
              <p>The Kist extension for Firefox and Chrome saves the image or page you’re on, with its source. Give it its own token.</p>
              {caps.isAdmin && (
                <button className="btn-icon" onClick={() => openSettings('tokens')}>
                  Make a token for it
                </button>
              )}
            </article>
            <article className="fr-card">
              <h3>
                <Bot size={16} aria-hidden="true" /> Let an AI agent help
              </h3>
              <p>Claude (or any MCP client) can search, add, tag and file for you, and can never delete for good:</p>
              <CopyCode text={mcpCommand(origin)} label="the MCP command" />
            </article>
          </>
        )}
      </div>
    </section>
  )
}

/** The sidebar checklist. Hidden once dismissed or once every step is done. */
export function SetupChecklist() {
  const caps = useCaps()
  const { mobile, openSettings } = useAppState()
  const [prefs, setPrefs] = useState(readSetup)
  const [open, setOpen] = useState(true)
  const { data: stats } = useStats()
  const batches = useBatches(caps.canEdit && !prefs.dismissed)
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: api.listTokens, enabled: caps.isAdmin && !prefs.dismissed, staleTime: 60_000 })
  const health = useQuery({ queryKey: ['archiveHealth'], queryFn: api.archiveHealth, enabled: caps.isAdmin && !prefs.dismissed, staleTime: 5 * 60_000 })

  // remember that this browser has run Kist from the home screen
  useEffect(() => {
    if (!prefs.standalone && window.matchMedia?.('(display-mode: standalone)').matches) {
      const next = { ...prefs, standalone: true }
      setPrefs(next)
      saveSetup(next)
    }
  }, [prefs])

  const steps = useMemo(
    () =>
      setupSteps({
        totalItems: stats?.total_items,
        actors: [...(batches.data ?? []).map((b) => b.actor), ...(tokens.data ?? []).filter((t) => t.last_used_at).map((t) => t.name)],
        sources: (batches.data ?? []).map((b) => b.source),
        standalone: prefs.standalone,
        backupOk: !!health.data?.backup.last_ok_at,
        manual: prefs.manual,
      }),
    [stats, batches.data, tokens.data, health.data, prefs],
  )
  const p = progress(steps)
  if (prefs.dismissed || !caps.canEdit || p.done === p.total || caps.restricted) return null

  const update = (next: typeof prefs) => {
    setPrefs(next)
    saveSetup(next)
  }
  const mark = (id: StepId, done: boolean) => update({ ...prefs, manual: { ...prefs.manual, [id]: done } })
  const r = 9
  const c = 2 * Math.PI * r

  return (
    <section className="setup-checklist" aria-labelledby="setup-h">
      <div className="section-head">
        <button className="setup-head-btn" aria-expanded={open} aria-controls="setup-steps" onClick={() => setOpen(!open)}>
          <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" className="setup-ring">
            <circle cx="11" cy="11" r={r} className="track" />
            <circle cx="11" cy="11" r={r} className="fill" strokeDasharray={`${(p.done / p.total) * c} ${c}`} transform="rotate(-90 11 11)" />
          </svg>
          <h2 id="setup-h">Set up Kist</h2>
          <span className="count">
            {p.done}/{p.total}
          </span>
          <ChevronDown size={14} className={open ? 'open' : ''} aria-hidden="true" />
        </button>
        <button className="icon-btn" aria-label="Hide the setup checklist" title="Hide for good" onClick={() => update({ ...prefs, dismissed: true })}>
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      {open && (
        <ol id="setup-steps" className="setup-steps">
          {steps.map((s) => {
            // only the next step to do shows its details, so the list stays short
            const next = s === steps.find((x) => !x.done)
            return (
            <li key={s.id} className={`${s.done ? 'done' : ''}${next ? ' next' : ''}`}>
              <span className="setup-tick" aria-hidden="true">
                {s.done ? <Check size={14} /> : null}
              </span>
              <span className="setup-text">
                <span className="setup-title">
                  {s.title}
                  <span className="sr-only">{s.done ? ' (done)' : ' (to do)'}</span>
                </span>
                {next && <span className="setup-detail">{s.detail}</span>}
                {next && s.id === 'agent' && !mobile && <CopyCode text={mcpCommand(window.location.origin)} label="the MCP command" />}
                {next && s.id === 'backup' && caps.isAdmin && (
                  <button className="small" onClick={() => openSettings('archive')}>
                    Open Archive health
                  </button>
                )}
                {next && !s.auto && s.id !== 'items' && (
                  <label className="check-inline setup-manual">
                    <input type="checkbox" checked={!!prefs.manual[s.id]} onChange={(e) => mark(s.id, e.target.checked)} />
                    Done
                  </label>
                )}
              </span>
            </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}

