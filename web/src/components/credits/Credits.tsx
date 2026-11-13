// F4: credits and rights in the web app — the bulk dialog (from the
// selection bar's More menu), the clearance warning (share and export
// dialogs, the board's Export menu), the info panel's rights rows and
// print check, the tile's AI-made badge, the sidebar count and the
// Settings switch. Logic lives in ../../credits.ts (pure, tested).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BadgeCheck, Bot, Printer, TriangleAlert, Wand2 } from 'lucide-react'
import { createContext, useContext, useId, useMemo, useState, type ReactNode } from 'react'
import { api, newBatchId, type Item } from '../../api'
import {
  aiReason,
  bulkCreditsPatch,
  clearanceText,
  creditLine,
  emptyBulkCredits,
  fitsText,
  isAiMade,
  needsCredit,
  profileText,
  RIGHTS_HINT,
  RIGHTS_STATUSES,
  RIGHTS_TEXT,
  rightsText,
  sizeText,
  statusBreakdown,
  type BulkCreditsForm,
  type CreditFilterKey,
  type Units,
} from '../../credits'
import { creditsApi, type ClearanceTarget } from '../../creditsApi'
import { useAfterUndo, useApplyItems, useCaps } from '../../data'
import { describeError } from '../../writes'
import type { BulkMenuItem } from '../BulkBar'
import { Modal } from '../Dialog'
import { useNotices } from '../Notices'

// ---------- provider: the bulk dialog ----------

interface CreditsApiCtx {
  openBulkCredits: (ids: string[]) => void
}

const Ctx = createContext<CreditsApiCtx | null>(null)

export function useCredits(): CreditsApiCtx {
  return useContext(Ctx) ?? { openBulkCredits: () => {} }
}

export function CreditsProvider({ children }: { children: ReactNode }) {
  const [bulk, setBulk] = useState<string[] | null>(null)
  const value = useMemo<CreditsApiCtx>(() => ({ openBulkCredits: (ids) => ids.length && setBulk(ids) }), [])
  return (
    <Ctx.Provider value={value}>
      {children}
      {bulk && <BulkCreditsDialog ids={bulk} onClose={() => setBulk(null)} />}
    </Ctx.Provider>
  )
}

/** The selection bar's More menu entry. */
export function useBulkCreditsMore(ids: string[], busy: boolean): BulkMenuItem[] {
  const { openBulkCredits } = useCredits()
  return [
    {
      key: 'credits',
      label: 'Credits & rights…',
      icon: BadgeCheck,
      disabled: busy,
      title: 'Set a credit, licence or rights status on all of them',
      run: () => openBulkCredits(ids),
    },
  ]
}

function BulkCreditsDialog({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [f, setF] = useState<BulkCreditsForm>(emptyBulkCredits)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const id = useId()
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  const qc = useQueryClient()
  const patch = bulkCreditsPatch(f)
  const set = <K extends keyof BulkCreditsForm>(k: K, v: BulkCreditsForm[K]) => setF((x) => ({ ...x, [k]: v }))

  const undo = (batch: string) =>
    api.undoBatch(batch).then(
      () => {
        afterUndo()
        void qc.invalidateQueries({ queryKey: ['credit-summary'] })
        notify({ key: 'credits', message: 'Undid the credits change' })
      },
      (e) => notify({ key: 'credits', tone: 'error', message: `Couldn't undo: ${describeError(e)}` }),
    )

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!Object.keys(patch).length) return
    setBusy(true)
    setError('')
    const batch = newBatchId()
    try {
      const r = await creditsApi.bulk(ids, patch, batch)
      afterUndo() // the items changed on the server: reload what's on screen
      void qc.invalidateQueries({ queryKey: ['credit-summary'] })
      const failed = r.failed.length ? `; ${r.failed.length} failed` : ''
      notify({
        key: 'credits',
        tone: r.failed.length ? 'error' : undefined,
        message: `Credits set on ${r.updated} item${r.updated === 1 ? '' : 's'}${failed}`,
        action: { label: 'Undo', run: () => void undo(r.batch_id || batch) },
      })
      onClose()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const text = (k: 'creator' | 'credit' | 'license', label: string, placeholder: string) => (
    <>
      <label htmlFor={`${id}-${k}`}>{label}</label>
      <input id={`${id}-${k}`} value={f[k]} onChange={(e) => set(k, e.target.value)} placeholder={placeholder} maxLength={2000} />
    </>
  )

  return (
    <Modal title={`Credits & rights for ${ids.length} items`} onClose={onClose} className="dialog cr-bulk-dialog">
      <form className="dialog-form" onSubmit={submit}>
        <p className="hint">Only the fields you fill in change. Everything else stays as it is on each item.</p>
        <div className="form-grid">
          {text('creator', 'Creator', 'Photographer, designer, artist')}
          {text('credit', 'Credit', 'e.g. Magnum Photos')}
          {text('license', 'Licence', 'e.g. CC BY 4.0')}
          <label htmlFor={`${id}-rs`}>Rights status</label>
          <select id={`${id}-rs`} value={f.rights_status} onChange={(e) => set('rights_status', e.target.value as BulkCreditsForm['rights_status'])}>
            <option value="">Leave as is</option>
            {RIGHTS_STATUSES.map((s) => (
              <option key={s} value={s}>
                {RIGHTS_TEXT[s]}
              </option>
            ))}
            <option value="clear">Clear (not set)</option>
          </select>
          <label htmlFor={`${id}-ex`}>Needs a credit</label>
          <select id={`${id}-ex`} value={f.credit_exempt} onChange={(e) => set('credit_exempt', e.target.value as BulkCreditsForm['credit_exempt'])}>
            <option value="">Leave as is</option>
            <option value="yes">No credit needed (own work…)</option>
            <option value="no">Needs a credit</option>
          </select>
        </div>
        {f.rights_status && f.rights_status !== 'clear' && <p className="hint">{RIGHTS_HINT[f.rights_status]}.</p>}
        <div className="error" role="alert">
          {error}
        </div>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={busy || !Object.keys(patch).length}>
            {busy ? 'Saving…' : 'Apply'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// ---------- the clearance warning ----------

/** Non-blocking: which items aren't licensed or cleared, before a share or
 * an export. Says nothing when all are, or when the user turned it off. */
export function ClearanceWarning({ target, compact = false }: { target: ClearanceTarget; compact?: boolean }) {
  const caps = useCaps()
  const key = ['clearance', target.kind, target.id, (target.itemIds ?? []).join(',')]
  const q = useQuery({
    queryKey: key,
    queryFn: () => creditsApi.clearance(target),
    enabled: caps.canEdit,
    retry: false,
    staleTime: 30_000,
  })
  const [open, setOpen] = useState(false)
  const line = clearanceText(q.data)
  if (!line || !q.data) return null
  const c = q.data
  const more = c.uncleared - c.items.length
  return (
    <div className={`cr-warning${compact ? ' cr-compact' : ''}`} role="note">
      <TriangleAlert size={16} aria-hidden="true" className="cr-warning-icon" />
      <div className="cr-warning-body">
        <p className="cr-warning-line">
          {line} <span className="hint">({statusBreakdown(c)})</span>
        </p>
        {!compact && (
          <>
            <button type="button" className="small" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              {open ? 'Hide the list' : 'Show them'}
            </button>
            {open && (
              <ul className="cr-warning-list">
                {c.items.map((i) => (
                  <li key={i.id}>
                    <span className="cr-file">{i.filename}</span>
                    <span className="hint"> · {rightsText(i.rights_status)}</span>
                  </li>
                ))}
                {more > 0 && <li className="hint">and {more} more</li>}
              </ul>
            )}
          </>
        )}
        <p className="hint">You can still go ahead. Turn this warning off in Settings → Sharing.</p>
      </div>
    </div>
  )
}

// ---------- info panel ----------

/** Rights status, the credit line and the AI-made reason, shown above the
 * provenance list; plus auto-fill from the item's own files. */
export function CreditsRows({ item, editable }: { item: Item; editable: boolean }) {
  const prov = item.provenance ?? {}
  const line = creditLine(prov)
  const ai = aiReason(prov)
  const apply = useApplyItems()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const info = useQuery({
    queryKey: ['item-credits', item.id, item.modified_at],
    queryFn: () => creditsApi.item(item.id),
    enabled: editable,
    retry: false,
    staleTime: 60_000,
  })
  const sugg = info.data?.suggestions ?? {}
  const fillable = Object.keys(sugg).filter((k) => k !== 'page_title')
  const fill = async () => {
    setBusy(true)
    setMsg('')
    try {
      const r = await creditsApi.autofill(item.id)
      apply([r.item])
      void qc.invalidateQueries({ queryKey: ['credit-summary'] })
      setMsg(Object.keys(r.filled).length ? `Filled in ${Object.keys(r.filled).join(', ').replace(/_/g, ' ')}.` : 'Nothing new to fill in.')
    } catch (e) {
      setMsg(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="cr-rows">
      <div className="meta-list">
        <div>
          <span>Credit</span>
          <span className={line ? 'source-text' : 'hint'}>
            {line ?? (prov.credit_exempt === true ? 'Not needed' : needsCredit(prov) ? 'Needs a credit' : '—')}
          </span>
        </div>
        <div>
          <span>Rights</span>
          <span className={`cr-status cr-status-${typeof prov.rights_status === 'string' ? prov.rights_status : 'none'}`}>
            {rightsText(prov.rights_status)}
          </span>
        </div>
      </div>
      {ai && (
        <p className="cr-ai-note">
          <Bot size={14} aria-hidden="true" /> AI-made: {ai}. Kist flags it; it is never hidden.
        </p>
      )}
      {editable && fillable.length > 0 && (
        <div className="cr-fill">
          <button type="button" className="btn-icon small" onClick={fill} disabled={busy}>
            <Wand2 size={14} aria-hidden="true" /> {busy ? 'Filling in…' : 'Fill in from the saved page and file'}
          </button>
          <span className="hint">
            Adds {fillable.map((k) => k.replace(/_/g, ' ')).join(', ')} where empty. Nothing is fetched.
          </span>
        </div>
      )}
      {msg && (
        <p className="hint" role="status">
          {msg}
        </p>
      )}
    </div>
  )
}

/** Inside the provenance edit form: rights status and "no credit needed". */
export function RightsFields({
  value,
  exempt,
  onChange,
  onExempt,
}: {
  value: string
  exempt: boolean
  onChange: (v: string) => void
  onExempt: (v: boolean) => void
}) {
  const id = useId()
  return (
    <>
      <div className="prov-field">
        <label htmlFor={`${id}-rs`}>Rights status</label>
        <select id={`${id}-rs`} value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={`${id}-rs-hint`}>
          <option value="">Not set</option>
          {RIGHTS_STATUSES.map((s) => (
            <option key={s} value={s}>
              {RIGHTS_TEXT[s]}
            </option>
          ))}
        </select>
        <span id={`${id}-rs-hint`} className="hint">
          {value && value in RIGHTS_HINT ? RIGHTS_HINT[value as keyof typeof RIGHTS_HINT] : 'Licensed and Cleared items share and export without a warning.'}
        </span>
      </div>
      <label className="check-row">
        <input type="checkbox" checked={exempt} onChange={(e) => onExempt(e.target.checked)} />
        No credit needed (own work, public domain…)
      </label>
    </>
  )
}

/** The print check: size at 300 and 150 dpi, the papers it fills, the
 * colour profile. Read from the original's header when the section opens. */
export function PrintCheck({ item }: { item: Item }) {
  const id = useId()
  const [units, setUnits] = useState<Units>(() => (navigator.language?.startsWith('en-US') ? 'in' : 'cm'))
  const q = useQuery({
    queryKey: ['print', item.id, item.sha256],
    queryFn: () => creditsApi.print(item.id),
    enabled: item.kind === 'image' && !!item.width,
    retry: false,
    staleTime: Infinity,
  })
  if (item.kind !== 'image' || !item.width || !item.height) return null
  const p = q.data
  return (
    <section className="detail-section cr-print" aria-labelledby={`${id}-h`}>
      <div className="section-head-row">
        <h3 id={`${id}-h`} className="section-label">
          <Printer size={14} aria-hidden="true" /> Print
        </h3>
        <div className="cr-units" role="group" aria-label="Units">
          {(['cm', 'in'] as Units[]).map((u) => (
            <button key={u} type="button" className="small" aria-pressed={units === u} onClick={() => setUnits(u)}>
              {u}
            </button>
          ))}
        </div>
      </div>
      {!p ? (
        <p className="hint">{q.isError ? 'Couldn’t read the file.' : 'Reading…'}</p>
      ) : (
        <div className="meta-list">
          <div>
            <span>At 300 dpi</span>
            <span>
              {sizeText(p, 300, units)} <span className="hint">· {fitsText(p, 300)}</span>
            </span>
          </div>
          <div>
            <span>At 150 dpi</span>
            <span>
              {sizeText(p, 150, units)} <span className="hint">· {fitsText(p, 150)}</span>
            </span>
          </div>
          <div>
            <span>Colour profile</span>
            <span>{profileText(p)}</span>
          </div>
          {p.file_dpi ? (
            <div>
              <span>File says</span>
              <span>{p.file_dpi} dpi</span>
            </div>
          ) : null}
          {p.low_res && (
            <div>
              <span>Low-res</span>
              <span className="cr-lowres">Under 5 in (12.7 cm) at 300 dpi</span>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// ---------- grid, sidebar, filters, settings ----------

/** Bottom-left on a tile: AI-made, from metadata or the user's say-so. */
export function AiBadge({ item }: { item: Item }) {
  if (!isAiMade(item.provenance)) return null
  return (
    <span className="badge cr-ai" aria-hidden="true" title={`AI-made: ${aiReason(item.provenance) ?? ''}`}>
      AI
    </span>
  )
}

export function useCreditSummary(enabled: boolean) {
  return useQuery({
    queryKey: ['credit-summary'],
    queryFn: creditsApi.summary,
    enabled,
    retry: false,
    staleTime: 30_000,
    refetchInterval: enabled ? 120_000 : false,
  })
}

/** The Filters panel's "Credits and print" fieldset. */
export function CreditFilterFields({
  f,
  set,
}: {
  f: Record<CreditFilterKey, string>
  set: (k: CreditFilterKey, v: string) => void
}) {
  const id = useId()
  return (
    <fieldset className="share-fieldset">
      <legend>Credits and print</legend>
      <div className="form-grid">
        <label htmlFor={`${id}-rs`}>Rights status</label>
        <select id={`${id}-rs`} value={f.rights_status} onChange={(e) => set('rights_status', e.target.value)}>
          <option value="">Any</option>
          <option value="uncleared">Not cleared (anything but Licensed/Cleared)</option>
          {RIGHTS_STATUSES.map((s) => (
            <option key={s} value={s}>
              {RIGHTS_TEXT[s]}
            </option>
          ))}
          <option value="none">Not set</option>
        </select>
        <label htmlFor={`${id}-nc`}>Credit</label>
        <select id={`${id}-nc`} value={f.needs_credit} onChange={(e) => set('needs_credit', e.target.value)}>
          <option value="">Any</option>
          <option value="true">Needs a credit</option>
          <option value="false">Has a credit (or needs none)</option>
        </select>
        <label htmlFor={`${id}-pmin`}>Prints at least</label>
        <input id={`${id}-pmin`} value={f.print_min} onChange={(e) => set('print_min', e.target.value)} placeholder="A4, 8in, 20cm" maxLength={20} />
        <label htmlFor={`${id}-pmax`}>Too small for</label>
        <input id={`${id}-pmax`} value={f.print_max} onChange={(e) => set('print_max', e.target.value)} placeholder="A4, 5in" maxLength={20} />
      </div>
      <p className="hint">Print sizes are at 300 dpi, either way round.</p>
    </fieldset>
  )
}

/** Settings → Credits. */
export function CreditsSettings() {
  const qc = useQueryClient()
  const caps = useCaps()
  const q = useQuery({ queryKey: ['credits-settings'], queryFn: creditsApi.settings, retry: false })
  const [error, setError] = useState('')
  const id = useId()
  if (!caps.canEdit) return null
  const on = q.data?.clearance_warning ?? true
  const toggle = async (v: boolean) => {
    setError('')
    try {
      qc.setQueryData(['credits-settings'], await creditsApi.setSettings({ clearance_warning: v }))
      void qc.invalidateQueries({ queryKey: ['clearance'] })
    } catch (e) {
      setError(describeError(e))
    }
  }
  return (
    <section className="settings-section cr-settings" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="section-label">
        Credits
      </h2>
      <label className="check-row">
        <input type="checkbox" checked={on} disabled={!q.data} onChange={(e) => void toggle(e.target.checked)} />
        Warn before sharing or exporting items that aren’t licensed or cleared
      </label>
      <p className="hint">
        The warning lists them and never stops you. Credits are printed on exports, share pages and slides either way. To fill in
        credits for items saved before Kist 2.0, run <code>kist credits-backfill</code> on the server (no network).
      </p>
      <div className="error" role="alert">
        {error}
      </div>
    </section>
  )
}
