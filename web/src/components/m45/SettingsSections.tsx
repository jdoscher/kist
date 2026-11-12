// Settings → Sharing (every share link, with views; revoke), Import (the
// built-in importers: kind, server path or Are.na URL, dry run, progress)
// and Phone & browser (the iOS Shortcut steps and the bookmarklet), plus
// the XMP sidecar note.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { useId, useState } from 'react'
import { useRefresh } from '../../data'
import { timeAgo } from '../../history'
import { api45, type ImportRun } from '../../m45/api45'
import { shareLink, shareStatusText, shareWhat, type Share } from '../../m45/share'
import { useAppState } from '../../state'
import { describeError } from '../../writes'
import { useDialog } from '../Dialog'
import { useNotices } from '../Notices'
import { CopyButton } from './ShareDialog'

// ---------- sharing ----------

export function SharingSection() {
  const shares = useQuery({ queryKey: ['shares'], queryFn: api45.listShares })
  const dialog = useDialog()
  const qc = useQueryClient()
  const { notify } = useNotices()
  const [busy, setBusy] = useState('')
  const list = [...(shares.data ?? [])].sort((a, b) => Number(a.status !== 'active') - Number(b.status !== 'active') || b.created_at.localeCompare(a.created_at))

  const revoke = async (s: Share) => {
    const ok = await dialog.confirm({
      title: `Revoke the link to “${s.title || shareWhat(s.target_kind, s.item_ids?.length)}”?`,
      message: 'Anyone who opens it afterwards is told it no longer works. This can’t be undone; make a new link instead.',
      confirmLabel: 'Revoke',
      danger: true,
    })
    if (!ok) return
    setBusy(s.id)
    try {
      await api45.revokeShare(s.id)
      await qc.invalidateQueries({ queryKey: ['shares'] })
      notify({ message: 'Link revoked.' })
    } catch (e) {
      notify({ message: `Couldn’t revoke it: ${describeError(e)}` })
    } finally {
      setBusy('')
    }
  }
  const remove = async (s: Share) => {
    setBusy(s.id)
    try {
      await api45.deleteShare(s.id)
      await qc.invalidateQueries({ queryKey: ['shares'] })
    } catch (e) {
      notify({ message: `Couldn’t delete it: ${describeError(e)}` })
    } finally {
      setBusy('')
    }
  }

  return (
    <section aria-labelledby="sharing-h">
      <div className="settings-section-head">
        <h2 id="sharing-h">Sharing</h2>
      </div>
      <p className="settings-intro">
        Read-only links to a folder, smart folder, board or a selection. Make one from the Share button above the grid, the selection bar or
        a board. Visitors see only what the link holds; views are counted without recording who.
      </p>
      {shares.isLoading && <p className="hint" role="status">Loading…</p>}
      {shares.isError && <p role="alert">Couldn’t load the links: {describeError(shares.error)}</p>}
      {shares.data && shares.data.length === 0 && <p className="hint">No share links yet.</p>}
      <ul className="token-list share-list">
        {list.map((s) => {
          const link = shareLink(s, window.location.origin)
          return (
            <li key={s.id} className={`token-row${s.status !== 'active' ? ' revoked' : ''}`}>
              <div className="token-main">
                <div className="token-name">
                  {s.title || `Untitled ${shareWhat(s.target_kind, s.item_ids?.length)}`}{' '}
                  <span className={`status-badge status-${s.status}`}>{shareStatusText(s)}</span>
                </div>
                <div className="hint">
                  {shareWhat(s.target_kind, s.item_ids?.length)} · {s.view_count} view{s.view_count === 1 ? '' : 's'}
                  {s.last_viewed_at ? `, last ${timeAgo(s.last_viewed_at)}` : ''} · {s.has_password ? 'password' : 'no password'} ·{' '}
                  {s.allow_download ? (s.allow_original ? 'originals' : 'downloads') : 'view only'} · made {timeAgo(s.created_at)} by {s.created_by}
                </div>
                {s.status === 'active' && <div className="mono share-url">{link}</div>}
              </div>
              <div className="token-actions">
                {s.status === 'active' && (
                  <>
                    <CopyButton text={link} label="Copy" />
                    <a className="icon-btn" href={link} target="_blank" rel="noreferrer" aria-label={`Open the link to ${s.title || 'this share'} (new tab)`} title="Open">
                      <ExternalLink size={14} aria-hidden="true" />
                    </a>
                    <button className="danger" disabled={busy === s.id} onClick={() => void revoke(s)}>
                      Revoke
                    </button>
                  </>
                )}
                {s.status !== 'active' && (
                  <button disabled={busy === s.id} onClick={() => void remove(s)}>
                    Remove
                  </button>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

// ---------- import ----------

export const IMPORT_KINDS: { id: string; label: string; source: string; help: string; url?: boolean }[] = [
  { id: 'eagle', label: 'Eagle', source: 'the .library folder', help: 'Folders, tags, ratings, notes, trash and simple smart folders.' },
  { id: 'pinterest', label: 'Pinterest', source: 'the account data export (.zip)', help: 'Your pins and boards, from Settings → Privacy and data → Request your data.' },
  { id: 'arena', label: 'Are.na', source: 'a channel export (.zip) or a public channel URL', help: 'Blocks with their sources and descriptions.', url: true },
  { id: 'raindrop', label: 'Raindrop.io', source: 'the CSV or HTML export', help: 'Bookmarks with their tags; images are downloaded.' },
  { id: 'takeout', label: 'Google Photos (Takeout)', source: 'the Takeout .zip or its folder', help: 'Dates, descriptions and places from the JSON beside each photo.' },
  { id: 'apple-photos', label: 'Apple Photos', source: 'a folder made by osxphotos export --sidecar json', help: 'Titles, keywords, favourites, dates and places.' },
  { id: 'sidecar', label: 'Files with sidecars', source: 'a folder of files with .json or .xmp beside them', help: 'Ratings, keywords and notes from Lightroom, darktable, digiKam or any tool that writes XMP.' },
]

function ImportReportView({ run }: { run: ImportRun }) {
  const r = run.report ?? {}
  const n = (v: unknown) => (typeof v === 'number' ? v.toLocaleString() : '0')
  return (
    <div className="import-report" role="status" aria-live="polite">
      <p>
        <strong>
          {run.status === 'pending' && 'Waiting for the worker…'}
          {run.status === 'running' && (run.dry_run ? 'Checking…' : 'Importing…')}
          {run.status === 'done' && (run.dry_run ? 'Dry run finished: nothing was changed.' : 'Import finished.')}
          {run.status === 'failed' && `The import failed: ${run.last_error ?? 'unknown error'}`}
        </strong>
      </p>
      {(run.status === 'pending' || run.status === 'running') && <progress aria-label="Import progress" />}
      <dl className="meta-list report-list">
        <div><dt>Found</dt><dd>{n(r.found)}</dd></div>
        <div><dt>{run.dry_run ? 'Would import' : 'Imported'}</dt><dd>{n(run.dry_run ? (r.found as number ?? 0) - ((r.duplicates as number) ?? 0) - ((r.skipped as number) ?? 0) - ((r.errors_count as number) ?? 0) : r.imported)}</dd></div>
        <div><dt>Already in Kist</dt><dd>{n(r.duplicates)}</dd></div>
        <div><dt>Skipped</dt><dd>{n(r.skipped)}</dd></div>
        {typeof r.to_download === 'number' && r.to_download > 0 && <div><dt>To download</dt><dd>{n(r.to_download)}</dd></div>}
        <div><dt>Errors</dt><dd>{n(r.errors_count)}</dd></div>
      </dl>
      {r.folder_path && !run.dry_run && <p className="hint">Filed into the folder {String(r.folder_path)}.</p>}
      {(r.errors ?? []).length > 0 && (
        <details>
          <summary>First errors</summary>
          <ul className="import-errors">
            {(r.errors ?? []).map((e, i) => (
              <li key={i}>
                <span className="mono">{e.key}</span>: {e.error}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

export function ImportSection() {
  const [kind, setKind] = useState('eagle')
  const [source, setSource] = useState('')
  const [folder, setFolder] = useState('')
  const [runId, setRunId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [upload, setUpload] = useState<{ name: string; pct: number } | null>(null)
  const refresh = useRefresh()
  const id = useId()
  const sendFile = async (file: File) => {
    setError('')
    setUpload({ name: file.name, pct: 0 })
    try {
      const r = await api45.uploadImport(file, (pct) => setUpload({ name: file.name, pct }))
      setSource(r.source)
      setUpload({ name: r.filename, pct: 100 })
    } catch (e) {
      setUpload(null)
      setError(describeError(e))
    }
  }
  const k = IMPORT_KINDS.find((x) => x.id === kind) ?? IMPORT_KINDS[0]
  const run = useQuery({
    queryKey: ['import', runId],
    queryFn: () => api45.getImport(runId!),
    enabled: runId !== null,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      if (s === 'done' && !q.state.data?.dry_run) refresh.itemsAdded()
      return s === 'done' || s === 'failed' ? false : 1500
    },
  })

  const start = async (dry: boolean) => {
    setBusy(true)
    setError('')
    try {
      const r = await api45.createImport({ kind, source: source.trim(), dry_run: dry, folder: folder.trim() || null })
      setRunId(r.id)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="import-h">
      <div className="settings-section-head">
        <h2 id="import-h">Import</h2>
      </div>
      <p className="settings-intro">
        Bring a collection over from another tool. Each run files into its own folder (Imported/&lt;kind&gt; &lt;date&gt;), keeps where each item
        came from, and is one batch in Recent changes, so Undo takes it all back. Try a dry run first: it counts what would come in, the
        duplicates and the errors, and changes nothing.
      </p>
      <form
        className="import-form"
        onSubmit={(e) => {
          e.preventDefault()
          void start(true)
        }}
      >
        <fieldset className="kind-picker">
          <legend>From</legend>
          {IMPORT_KINDS.map((x) => (
            <label key={x.id} className={`kind-option${kind === x.id ? ' on' : ''}`}>
              <input type="radio" name={`${id}-kind`} value={x.id} checked={kind === x.id} onChange={() => setKind(x.id)} />
              <span className="kind-label">{x.label}</span>
              <span className="hint">{x.help}</span>
            </label>
          ))}
        </fieldset>
        <label htmlFor={`${id}-file`}>Upload the export from this computer</label>
        <input
          id={`${id}-file`}
          type="file"
          accept=".zip,.csv,.html,.htm,.json"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void sendFile(f)
            e.target.value = ''
          }}
        />
        {upload && (
          <p className="hint" role="status">
            {upload.pct < 100 ? `Uploading ${upload.name}… ${upload.pct}%` : `Uploaded ${upload.name}.`}
            {upload.pct < 100 && <progress max={100} value={upload.pct} aria-label="Upload progress" />}
          </p>
        )}
        <label htmlFor={`${id}-src`}>{k.url ? 'Or a path on the server, or the channel URL' : 'Or a path on the server (folders, big exports)'}</label>
        <input
          id={`${id}-src`}
          className="mono"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder={k.url ? '/library/import/arena.zip or https://www.are.na/…' : '/library/import/…'}
          aria-describedby={`${id}-src-hint`}
          spellCheck={false}
        />
        <p id={`${id}-src-hint`} className="hint">
          {k.label}: {k.source}. A folder, or an export too big to upload, goes in the library’s <code>import</code> folder (or the inbox
          folder) on the server first; Kist only reads from those two.
        </p>
        <label htmlFor={`${id}-folder`}>Folder name (optional)</label>
        <input id={`${id}-folder`} value={folder} onChange={(e) => setFolder(e.target.value)} placeholder={`${k.label} ${new Date().toISOString().slice(0, 10)}`} maxLength={200} />
        <div className="error" role="alert">
          {error}
        </div>
        <div className="dialog-actions">
          <button type="submit" disabled={busy || !source.trim()}>
            Dry run
          </button>
          <button type="button" className="btn-primary" disabled={busy || !source.trim() || !run.data?.dry_run || run.data.status !== 'done'} onClick={() => void start(false)} title={run.data?.dry_run && run.data.status === 'done' ? 'Import for real' : 'Do a dry run first'}>
            Import
          </button>
        </div>
      </form>
      {run.data && <ImportReportView run={run.data} />}

      <h3 className="section-label settings-sub">XMP sidecars</h3>
      <p className="settings-intro">
        Kist writes an <code>original.xmp</code> beside every image in the library with its rating, keywords, notes, label, rights and
        provenance, and reads XMP from files (and from a <code>.xmp</code> dropped next to a file in the inbox), so Lightroom, darktable and
        digiKam see the same ratings and tags. It is on by default; set <code>KIST_XMP_SIDECARS=0</code> in <code>.env</code> to stop writing
        them. <code>kist xmp --export DIR</code> writes <code>&lt;name&gt;.xmp</code> beside exported files.
      </p>
    </section>
  )
}

// ---------- phone and browser ----------

export function PhoneSection() {
  const sc = useQuery({ queryKey: ['shortcut'], queryFn: api45.shortcut })
  const { openSettings } = useAppState()
  return (
    <section aria-labelledby="phone-h">
      <div className="settings-section-head">
        <h2 id="phone-h">Phone & browser</h2>
      </div>
      <h3 className="section-label settings-sub">iPhone and iPad: Save to Kist from the share sheet</h3>
      <p className="settings-intro">
        A Shortcut sends photos, files and links to Kist from any app’s share sheet. Give it its own add-only token (
        <button type="button" className="linkish" onClick={() => openSettings('tokens')}>
          Tokens
        </button>{' '}
        → New token → Add only), so the phone can save but never browse or delete.
      </p>
      {sc.isLoading && <p className="hint" role="status">Loading…</p>}
      {sc.isError && <p role="alert">Couldn’t load the steps: {describeError(sc.error)}</p>}
      {sc.data && (
        <>
          <p className="hint">
            Address: <span className="mono">{sc.data.endpoint}</span> <CopyButton text={sc.data.endpoint} label="Copy" />
          </p>
          <ol className="steps">
            {sc.data.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          <p className="hint">{sc.data.why}</p>
        </>
      )}
      <p className="hint">
        The full walk-through with screenshots is in <span className="mono">docs/ios-shortcut.md</span> in Kist’s repository.
      </p>

      <h3 className="section-label settings-sub">Any browser: the Save to Kist bookmarklet</h3>
      <p className="settings-intro">
        For Safari and browsers without the extension: a bookmark that sends the page’s main image to a small Kist window where you confirm
        it. It never holds a token.
      </p>
      <p>
        <a href="/bookmarklet" target="_blank" rel="noreferrer">
          Get the bookmarklet <ExternalLink size={13} strokeWidth={1.75} aria-hidden="true" />
          <span className="sr-only"> (new tab)</span>
        </a>
      </p>
    </section>
  )
}
