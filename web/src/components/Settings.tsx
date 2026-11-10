import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Check, Copy, Cpu, KeyRound, ShieldCheck, Sparkles, TriangleAlert, X } from 'lucide-react'
import { lazy, Suspense, useId, useState } from 'react'
import { api, emptyQuery, type ApiToken, type ArchiveHealth } from '../api'
import { useCaps, useFolderNames, useFolders, useMe, useStats } from '../data'
import { fmtAgeHours, fmtBytes, percent } from '../format'
import { timeAgo } from '../history'
import { useAppState } from '../state'
import { helperCommand, PRESETS, scopeText, tokenRequest, tokenStatus, type PresetId } from '../tokens'
import type { SettingsSection } from '../url'
import { useWrite } from '../useWrite'
import { Mark, Wordmark } from './Brand'
import { Modal, useDialog } from './Dialog'
import { useNotices } from './Notices'
import { AiExtras } from './AiExtras'

const VocabularySection = lazy(() => import('./TagSettings').then((m) => ({ default: m.VocabularySection })))
const CleanupSection = lazy(() => import('./TagSettings').then((m) => ({ default: m.CleanupSection })))

const SECTIONS: { id: SettingsSection; label: string; icon: typeof KeyRound }[] = [
  { id: 'tokens', label: 'Tokens', icon: KeyRound },
  { id: 'archive', label: 'Archive health', icon: ShieldCheck },
  { id: 'ai', label: 'AI', icon: Cpu },
  { id: 'vocabulary', label: 'Vocabulary', icon: BookOpen },
  { id: 'cleanup', label: 'Tag cleanup', icon: Sparkles },
]

const dateText = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : '')

/** Settings: tokens, archive health and AI status. The owner's (admin)
 * screen; other tokens are told it needs the owner's token. */
export function SettingsScreen() {
  const { settings, openSettings, closeSettings, mobile } = useAppState()
  const caps = useCaps()
  const section = settings ?? 'tokens'
  return (
    <div className={`settings-screen${mobile ? ' mobile' : ''}`}>
      <header className="settings-head">
        <div className="brand" title="Kist">
          <Mark />
          <Wordmark />
        </div>
        <h1 className="settings-title">Settings</h1>
        <button className="btn-icon settings-close" onClick={closeSettings}>
          <X size={16} strokeWidth={1.75} aria-hidden="true" />
          Back to library
        </button>
      </header>
      {!caps.isAdmin ? (
        <main className="settings-body">
          <p>Settings need the owner’s token (or one with full access).</p>
        </main>
      ) : (
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="Settings sections">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                className={section === s.id ? 'on' : ''}
                aria-current={section === s.id ? 'page' : undefined}
                onClick={() => openSettings(s.id)}
              >
                <s.icon size={16} strokeWidth={1.75} aria-hidden="true" />
                {s.label}
              </button>
            ))}
          </nav>
          <main className="settings-body">
            {section === 'tokens' && <TokensSection />}
            {section === 'archive' && <ArchiveSection />}
            {section === 'ai' && <AiSection />}
            <Suspense fallback={<p className="hint" role="status">Loading…</p>}>
              {section === 'vocabulary' && <VocabularySection />}
              {section === 'cleanup' && <CleanupSection />}
            </Suspense>
          </main>
        </div>
      )}
    </div>
  )
}

// ---------- tokens ----------

function TokensSection() {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['tokens'], queryFn: api.listTokens })
  const { data: me } = useMe()
  const folderName = useFolderNames()
  const dialog = useDialog()
  const { notify } = useNotices()
  const qc = useQueryClient()
  const w = useWrite()
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<(ApiToken & { token: string }) | null>(null)

  const revoke = async (t: ApiToken) => {
    const ok = await dialog.confirm({
      title: `Revoke “${t.name}”?`,
      message: 'Anything using this token stops working at once. This can’t be undone; make a new token instead.',
      confirmLabel: 'Revoke',
      danger: true,
    })
    if (!ok) return
    void w.run(`revoke:${t.id}`, () => api.revokeToken(t.id), {
      what: 'revoke the token',
      onDone: () => {
        void qc.invalidateQueries({ queryKey: ['tokens'] })
        notify({ message: `Revoked “${t.name}”.` })
      },
    })
  }

  const now = Date.now()
  const tokens = [...(data ?? [])].sort((a, b) => Number(!!a.revoked_at) - Number(!!b.revoked_at))
  return (
    <section aria-labelledby="tokens-h">
      <div className="settings-section-head">
        <h2 id="tokens-h">Tokens</h2>
        <button className="primary" onClick={() => setCreating(true)}>
          New token
        </button>
      </div>
      <p className="settings-intro">
        Give each phone, person and agent its own token, with only the access it needs. You can revoke one without
        touching the others, and every change a token makes shows under its name in an item’s history. You’re signed in
        as <strong>{me?.name ?? 'owner'}</strong>; the owner’s token (<code>KIST_TOKEN</code> in <code>.env</code>)
        isn’t listed here.
      </p>
      {isLoading && (
        <p className="hint" role="status">
          Loading…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load the tokens: {String((error as Error).message)}</p>}
      {data && data.length === 0 && <p className="hint">No named tokens yet.</p>}
      <ul className="token-list">
        {tokens.map((t) => {
          const status = tokenStatus(t, now)
          return (
            <li key={t.id} className={`token-card ${status}`}>
              <div className="token-top">
                <h3 className="token-name">{t.name}</h3>
                <span className={`pill ${status}`}>
                  {status === 'active' ? 'Active' : status === 'revoked' ? 'Revoked' : 'Expired'}
                </span>
              </div>
              <dl className="token-facts">
                <div>
                  <dt>Access</dt>
                  <dd>{scopeText(t.scopes, t.folder_ids)}</dd>
                </div>
                {t.folder_ids.length > 0 && (
                  <div>
                    <dt>Folders</dt>
                    <dd>{t.folder_ids.map((f) => folderName.get(f) ?? 'a deleted folder').join(', ')}</dd>
                  </div>
                )}
                <div>
                  <dt>Created</dt>
                  <dd>{dateText(t.created_at)}</dd>
                </div>
                <div>
                  <dt>Last used</dt>
                  <dd>{t.last_used_at ? timeAgo(t.last_used_at, now) : 'never'}</dd>
                </div>
                <div>
                  <dt>Expires</dt>
                  <dd>{t.expires_at ? dateText(t.expires_at) : 'never'}</dd>
                </div>
                {t.revoked_at && (
                  <div>
                    <dt>Revoked</dt>
                    <dd>{dateText(t.revoked_at)}</dd>
                  </div>
                )}
              </dl>
              {status === 'active' && (
                <button
                  className="danger"
                  disabled={w.busy(`revoke:${t.id}`)}
                  onClick={() => void revoke(t)}
                  aria-label={`Revoke ${t.name}`}
                >
                  Revoke
                </button>
              )}
            </li>
          )
        })}
      </ul>
      {creating && (
        <NewTokenDialog
          onClose={() => setCreating(false)}
          onCreated={(t) => {
            setCreating(false)
            setCreated(t)
            void qc.invalidateQueries({ queryKey: ['tokens'] })
          }}
        />
      )}
      {created && <ShowTokenDialog token={created} onClose={() => setCreated(null)} />}
    </section>
  )
}

function NewTokenDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (t: ApiToken & { token: string }) => void
}) {
  const [name, setName] = useState('')
  const [preset, setPreset] = useState<PresetId>('family')
  const [folders, setFolders] = useState<string[]>([])
  const [expires, setExpires] = useState('')
  const { data: allFolders } = useFolders()
  const w = useWrite()
  const id = useId()
  const chosen = PRESETS.find((p) => p.id === preset)!
  const ok = name.trim().length > 0

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!ok) return
    void w.run('create', () => api.createToken(tokenRequest(name, preset, folders, expires || null)), {
      what: 'make the token',
      onDone: onCreated,
    })
  }

  return (
    <Modal title="New token" onClose={onClose} className="token-dialog">
      <form className="dialog-form" onSubmit={submit}>
        <label htmlFor={`${id}-name`}>Name</label>
        <input
          id={`${id}-name`}
          data-autofocus
          value={name}
          maxLength={100}
          placeholder="e.g. Patty’s iPad, iPhone Shortcut, Claude"
          onChange={(e) => setName(e.target.value)}
        />
        <fieldset className="preset-list">
          <legend>What can it do?</legend>
          {PRESETS.map((p) => (
            <label key={p.id} className={`preset${preset === p.id ? ' on' : ''}`}>
              <input
                type="radio"
                name={`${id}-preset`}
                value={p.id}
                checked={preset === p.id}
                onChange={() => setPreset(p.id)}
                aria-describedby={`${id}-${p.id}-explain`}
              />
              <span className="preset-text">
                <span className="preset-label">{p.label}</span>
                <span id={`${id}-${p.id}-explain`} className="preset-explain">
                  {p.explain}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
        {chosen.folders && (
          <fieldset className="folder-picks">
            <legend>Only these folders (optional)</legend>
            {(allFolders ?? []).length === 0 && <p className="hint">No folders yet: it will see the whole library.</p>}
            {(allFolders ?? []).map((f) => (
              <label key={f.id} className="check-row">
                <input
                  type="checkbox"
                  checked={folders.includes(f.id)}
                  onChange={(e) =>
                    setFolders((cur) => (e.target.checked ? [...cur, f.id] : cur.filter((x) => x !== f.id)))
                  }
                />
                {f.name}
              </label>
            ))}
          </fieldset>
        )}
        <label htmlFor={`${id}-exp`}>Expires (optional)</label>
        <input id={`${id}-exp`} type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={!ok || w.busy()}>
            Make token
          </button>
        </div>
      </form>
    </Modal>
  )
}

function CopyField({ value, label, autoFocus = false }: { value: string; label: string; autoFocus?: boolean }) {
  const [copied, setCopied] = useState(false)
  const id = useId()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      // no clipboard permission (plain http): select it for ⌘C
      const el = document.getElementById(id) as HTMLInputElement | null
      el?.select()
    }
  }
  return (
    <div className="copy-field">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input id={id} readOnly value={value} onFocus={(e) => e.currentTarget.select()} />
      <button
        className="btn-icon"
        onClick={() => void copy()}
        aria-label={`Copy ${label}`}
        data-autofocus={autoFocus || undefined}
      >
        {copied ? (
          <Check size={14} strokeWidth={2} aria-hidden="true" />
        ) : (
          <Copy size={14} strokeWidth={1.75} aria-hidden="true" />
        )}
        {copied ? 'Copied' : 'Copy'}
      </button>
      <span className="sr-only" role="status">
        {copied ? 'Copied to the clipboard' : ''}
      </span>
    </div>
  )
}

function ShowTokenDialog({ token, onClose }: { token: ApiToken & { token: string }; onClose: () => void }) {
  const origin = window.location.origin
  const helper = token.scopes.includes('helper')
  const add = token.scopes.includes('add')
  const agent = token.scopes.includes('agent')
  return (
    <Modal title={`Token for “${token.name}”`} onClose={onClose} className="token-dialog">
      <p className="warn-box" role="note">
        <TriangleAlert size={16} strokeWidth={1.75} aria-hidden="true" />
        Copy it now. Kist keeps only a fingerprint of it, so it can’t show it again. Anyone who has it can use Kist as “
        {scopeText(token.scopes, token.folder_ids)}”, so keep it private.
      </p>
      <CopyField value={token.token} label="the token" autoFocus />
      {helper && (
        <>
          <p>On the Mac, run:</p>
          <CopyField value={helperCommand(origin, token.token)} label="the helper command" />
        </>
      )}
      {add && (
        <p className="hint">
          Send files or JSON <code>{'{"url": …}'}</code> to <code>{origin}/api/items</code> with the header{' '}
          <code>Authorization: Bearer</code> and this token.
        </p>
      )}
      {agent && (
        <p className="hint">
          Add Kist to Claude with{' '}
          <code>claude mcp add --transport http kist {origin}/mcp --header "Authorization: Bearer …"</code>.
        </p>
      )}
      <div className="dialog-actions">
        <button className="primary" onClick={onClose}>
          Done
        </button>
      </div>
    </Modal>
  )
}

// ---------- archive health ----------

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function Status({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span className={`status-text ${ok ? 'ok' : 'bad'}`}>
      {ok ? (
        <Check size={14} strokeWidth={2} aria-hidden="true" />
      ) : (
        <TriangleAlert size={14} strokeWidth={1.75} aria-hidden="true" />
      )}
      {children}
    </span>
  )
}

function ArchiveSection() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['archiveHealth'],
    queryFn: api.archiveHealth,
    refetchInterval: 60_000,
  })
  const { setQuery } = useAppState()
  const qc = useQueryClient()
  const w = useWrite()
  const id = useId()

  if (isLoading)
    return (
      <p className="hint" role="status">
        Loading…
      </p>
    )
  if (isError || !data) return <p role="alert">Couldn’t load archive health: {String((error as Error)?.message)}</p>

  const { fixity, verify, backup, disk, checklist } = data
  const pct = percent(fixity.checked_30d, fixity.total)
  const setOffsite = (done: boolean) =>
    void w.run('offsite', () => api.setChecklist('offsite', done), {
      what: 'save the checklist',
      onDone: (h: ArchiveHealth) => qc.setQueryData(['archiveHealth'], h),
    })
  const done321 = checklist.filter((c) => c.done).length

  return (
    <section aria-labelledby="archive-h">
      <div className="settings-section-head">
        <h2 id="archive-h">Archive health</h2>
      </div>
      <p className="settings-intro">
        Is every file still exactly as it was added, and does a second copy exist? Kist re-checks each original’s
        fingerprint about once a month and can back the library up with restic.
      </p>

      <div className="health-card">
        <h3>Files intact (fixity)</h3>
        <p>
          {fixity.checked_30d.toLocaleString()} of {fixity.total.toLocaleString()} originals checked in the last 30
          days.
        </p>
        <progress id={`${id}-fix`} max={100} value={pct} aria-label={`Fixity checked in the last 30 days: ${pct}%`} />
        {fixity.failed.length === 0 ? (
          <Status ok>No file has changed.</Status>
        ) : (
          <>
            <Status ok={false}>
              {fixity.failed.length === 1
                ? '1 original no longer matches its fingerprint.'
                : `${fixity.failed.length} originals no longer match their fingerprints.`}
            </Status>
            <ul className="failed-list">
              {fixity.failed.slice(0, 20).map((f) => (
                <li key={f.id}>
                  <span className="failed-name">{f.filename}</span>
                  <span className="mono">expected SHA-256 {f.expected_sha256}</span>
                </li>
              ))}
            </ul>
            <p className="hint">
              Find the copy with that fingerprint in a backup and put it back in the item’s folder.
            </p>
            <button onClick={() => setQuery({ ...emptyQuery, fixity: 'failed' })}>Show them in the library</button>
          </>
        )}
      </div>

      <div className="health-card">
        <h3>Database and files agree (verify)</h3>
        <dl className="facts">
          <Fact label="Last run">
            {verify.last_run ? timeAgo(verify.last_run) : 'not yet (the worker runs it daily)'}
          </Fact>
          {verify.items_checked != null && <Fact label="Items checked">{verify.items_checked.toLocaleString()}</Fact>}
          <Fact label="Result">
            {verify.last_run ? (
              <Status ok={verify.ok}>
                {verify.ok
                  ? 'No problems'
                  : `${String(verify.drift)} problem${verify.drift === 1 ? '' : 's'}: run kist verify to see them`}
              </Status>
            ) : (
              '—'
            )}
          </Fact>
        </dl>
      </div>

      <div className="health-card">
        <h3>Backups</h3>
        <dl className="facts">
          <Fact label="Backup repository">
            <Status ok={backup.repo_set}>{backup.repo_set ? 'Configured' : 'Not configured'}</Status>
          </Fact>
          <Fact label="Last backup">
            {backup.last_at ? (
              <Status ok={backup.ok}>
                {backup.ok ? 'Succeeded' : 'Failed'} {timeAgo(backup.last_at)}
              </Status>
            ) : (
              'never'
            )}
          </Fact>
          <Fact label="Last good backup">{backup.last_ok_at ? `${fmtAgeHours(backup.age_h)} ago` : 'never'}</Fact>
          {backup.error && !backup.ok && <Fact label="Error">{backup.error}</Fact>}
          {backup.last_at && backup.db_dump_ok === false && (
            <Fact label="Database dump">
              <Status ok={false}>Skipped: {backup.db_dump_error ?? 'unknown error'}</Status>
            </Fact>
          )}
          <Fact label="Schedule">
            {backup.interval_hours ? `every ${backup.interval_hours} hours` : 'manual (kist backup)'}
          </Fact>
          <Fact label="Free disk">{disk.free_bytes != null ? fmtBytes(disk.free_bytes) : 'unknown'}</Fact>
          {data.trash_retention_days != null && (
            <Fact label="Trash empties after">
              {data.trash_retention_days ? `${data.trash_retention_days} days` : 'never'}
            </Fact>
          )}
        </dl>
        {!backup.repo_set && (
          <details className="howto" open>
            <summary>How to set up backups</summary>
            <ol>
              <li>
                Pick where the copy goes: a disk on another machine (<code>sftp:user@host:/backups/kist</code>), a cloud
                bucket (<code>s3:…</code>, <code>b2:…</code>) or a mounted drive (<code>/mnt/backup/kist</code>).
              </li>
              <li>
                In Kist’s <code>.env</code>, set <code>KIST_BACKUP_REPO</code> to it, and{' '}
                <code>RESTIC_PASSWORD_FILE</code> (or <code>RESTIC_PASSWORD</code>) to the password that encrypts it.
                Keep that password somewhere else too: without it the backup can’t be read.
              </li>
              <li>
                Set <code>KIST_BACKUP_INTERVAL_HOURS=24</code> for a nightly backup, then{' '}
                <code>docker compose up -d</code>. To run one now: <code>docker compose exec worker kist backup</code>.
              </li>
            </ol>
          </details>
        )}
      </div>

      <div className="health-card">
        <h3>
          3-2-1 checklist{' '}
          <span className="count">
            ({done321} of {checklist.length})
          </span>
        </h3>
        <p className="hint">Three copies, on two kinds of storage, one of them somewhere else.</p>
        <ul className="checklist">
          {checklist.map((c) =>
            c.id === 'offsite' ? (
              <li key={c.id}>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={c.done}
                    disabled={w.busy('offsite')}
                    onChange={(e) => setOffsite(e.target.checked)}
                    aria-describedby={`${id}-offsite`}
                  />
                  {c.label}
                </label>
                <span id={`${id}-offsite`} className="hint">
                  Kist can’t check this one: tick it when a copy lives in another building or a cloud.
                </span>
              </li>
            ) : (
              <li key={c.id}>
                <Status ok={c.done}>{c.label}</Status>
              </li>
            ),
          )}
        </ul>
      </div>
    </section>
  )
}

// ---------- AI ----------

function AiSection() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['aiStatus'],
    queryFn: api.aiStatus,
    refetchInterval: 10_000,
  })
  const { data: stats } = useStats()
  const { openSettings } = useAppState()
  const origin = window.location.origin

  return (
    <section aria-labelledby="ai-h">
      <div className="settings-section-head">
        <h2 id="ai-h">AI</h2>
      </div>
      <p className="settings-intro">
        Kist looks at every image and reads the text in screenshots and PDFs, entirely on your own hardware, so search
        can find things by what’s in them. The results are kept as plain files next to each item. New items are done as
        they arrive; older ones when you run <code>kist ai-backfill</code>.
      </p>
      {isLoading && (
        <p className="hint" role="status">
          Loading…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load the AI status: {String((error as Error).message)}</p>}
      {data && (
        <>
          <div className="health-card">
            <h3>Status</h3>
            <dl className="facts">
              <Fact label="AI">
                <Status ok={data.enabled}>{data.enabled ? 'On' : 'Off (KIST_AI_ENABLED)'}</Status>
              </Fact>
              <Fact label="Model">
                <span className="mono">{data.model}</span>
              </Fact>
              <Fact label="ml service">
                <Status ok={data.ml_ok}>
                  {data.ml_ok
                    ? 'Reachable'
                    : data.ml_url_set === false
                      ? 'Not configured (KIST_ML_URL)'
                      : 'Not reachable'}
                </Status>
              </Fact>
              <Fact label="Images understood">
                {data.embedded.toLocaleString()}
                {stats ? ` of ${stats.total_items.toLocaleString()} items` : ''}
              </Fact>
              <Fact label="Text read (OCR, PDFs)">{data.ocr_done.toLocaleString()} items</Fact>
              <Fact label="Waiting">
                {data.jobs
                  ? Object.entries(data.jobs)
                      .map(
                        ([kind, by]) =>
                          `${(by.pending ?? 0).toLocaleString()} ${kind === 'embed' ? 'to understand' : kind === 'ocr' ? 'to read' : kind === 'caption' ? 'to caption' : kind === 'faces' ? 'to look for faces' : kind}`,
                      )
                      .join(', ')
                  : data.pending.toLocaleString()}
              </Fact>
              <Fact label="Hours">
                {data.window
                  ? `${data.window} (${data.window_open ? 'open now' : 'closed now: backfill waits'})`
                  : 'any time'}
              </Fact>
            </dl>
          </div>
          <AiExtras data={data} />
          <div className="health-card">
            <h3>Helpers</h3>
            {data.helpers.length === 0 ? (
              <p className="hint">No helper has checked in.</p>
            ) : (
              <ul className="helper-list">
                {data.helpers.map((h) => (
                  <li key={h.name}>
                    <strong>{h.name}</strong>
                    <span className="hint">
                      {' '}
                      last seen {h.last_seen ? timeAgo(h.last_seen) : 'never'}
                      {h.jobs_done != null ? ` · ${h.jobs_done.toLocaleString()} jobs done` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p>
              A Mac can work through the backlog for this server. Install Kist with its model extras (
              <code>pip install 'kist[ml]'</code>), make a token with the <strong>Helper</strong> preset, then run:
            </p>
            <pre className="code-block">{`kist helper --url ${origin} --token <helper token>`}</pre>
            <button onClick={() => openSettings('tokens')}>Make a helper token</button>
          </div>
        </>
      )}
    </section>
  )
}
