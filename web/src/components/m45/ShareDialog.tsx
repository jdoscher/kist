// Share a folder, smart folder, board or selection as a read-only link
// (/s/<slug>). Defaults: no password, expires in 30 days, no downloads.

import { useQueryClient } from '@tanstack/react-query'
import { Check, Copy, ExternalLink } from 'lucide-react'
import { useId, useState } from 'react'
import { api45 } from '../../m45/api45'
import {
  defaultShareForm,
  EXPIRY_CHOICES,
  shareFormErrors,
  shareLink,
  sharePayload,
  shareStatusText,
  shareWhat,
  type ExpiryChoice,
  type Share,
  type ShareForm,
} from '../../m45/share'
import { describeError } from '../../writes'
import { Modal } from '../Dialog'
import type { Target } from './M45'

export function CopyButton({ text, label = 'Copy link' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="btn-icon"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true)
          setTimeout(() => setDone(false), 1800)
        })
      }}
    >
      {done ? <Check size={14} strokeWidth={2} aria-hidden="true" /> : <Copy size={14} strokeWidth={1.75} aria-hidden="true" />}
      {done ? 'Copied' : label}
      <span className="sr-only" role="status">
        {done ? 'Copied to the clipboard' : ''}
      </span>
    </button>
  )
}

export default function ShareDialog({ target, onClose }: { target: Target; onClose: () => void }) {
  const kind = target.kind === 'all' ? 'items' : target.kind
  const [form, setForm] = useState<ShareForm>(() => defaultShareForm(kind, target.id, target.itemIds, target.name))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState<Share | null>(null)
  const [tried, setTried] = useState(false)
  const qc = useQueryClient()
  const id = useId()
  const set = (patch: Partial<ShareForm>) => setForm((f) => ({ ...f, ...patch }))
  const errors = shareFormErrors(form)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (errors.length) return
    setBusy(true)
    setError('')
    try {
      const s = await api45.createShare(sharePayload(form))
      setCreated(s)
      void qc.invalidateQueries({ queryKey: ['shares'] })
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const what = shareWhat(kind, target.itemIds.length)
  if (created) {
    const link = shareLink(created, window.location.origin)
    return (
      <Modal title="Link ready" onClose={onClose} className="dialog share-dialog">
        <div className="dialog-form">
          <p>
            Anyone with this link can see the {what}
            {created.has_password ? ' after entering the password' : ''}. {shareStatusText(created)}.
          </p>
          <label htmlFor={`${id}-link`}>Link</label>
          <div className="copy-row">
            <input id={`${id}-link`} className="mono" readOnly value={link} onFocus={(e) => e.currentTarget.select()} />
            <CopyButton text={link} />
          </div>
          <p className="hint">
            {created.allow_download ? (created.allow_original ? 'Visitors can download the originals.' : 'Visitors can download display copies.') : 'Visitors can look but not download.'}{' '}
            Revoke it any time under Settings → Sharing.
          </p>
          <div className="dialog-actions">
            <a className="btn-icon" href={link} target="_blank" rel="noreferrer">
              Open <ExternalLink size={13} strokeWidth={1.75} aria-hidden="true" />
              <span className="sr-only"> (new tab)</span>
            </a>
            <button type="button" className="primary" data-autofocus onClick={onClose}>
              Done
            </button>
          </div>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title={`Share ${target.name ? `“${target.name}”` : `the ${what}`}`} onClose={onClose} className="dialog share-dialog">
      <form className="dialog-form" onSubmit={submit} noValidate>
        <p className="hint">A read-only page anyone with the link can open. Nothing else in Kist is reachable from it.</p>
        <label htmlFor={`${id}-title`}>Title on the page</label>
        <input
          id={`${id}-title`}
          data-autofocus
          value={form.title}
          maxLength={200}
          onChange={(e) => set({ title: e.target.value })}
          placeholder={target.name || 'Shared from Kist'}
        />

        <fieldset className="share-fieldset">
          <legend>Access</legend>
          <label className="check-row">
            <input type="checkbox" checked={form.usePassword} onChange={(e) => set({ usePassword: e.target.checked })} />
            Ask for a password
          </label>
          {form.usePassword && (
            <>
              <label htmlFor={`${id}-pw`}>Password</label>
              <input
                id={`${id}-pw`}
                type="text"
                autoComplete="off"
                spellCheck={false}
                value={form.password}
                onChange={(e) => set({ password: e.target.value })}
                aria-describedby={`${id}-pw-hint`}
                aria-invalid={tried && form.password.length < 4}
              />
              <span id={`${id}-pw-hint`} className="hint">
                At least 4 characters. Send it separately from the link.
              </span>
            </>
          )}
          <label htmlFor={`${id}-exp`}>Link stops working</label>
          <select id={`${id}-exp`} value={form.expiry} onChange={(e) => set({ expiry: e.target.value as ExpiryChoice })}>
            {EXPIRY_CHOICES.map(([v, label]) => (
              <option key={v} value={v}>
                {v === 'never' ? 'Never' : v === 'date' ? label : `After ${label}`}
              </option>
            ))}
          </select>
          {form.expiry === 'date' && (
            <>
              <label htmlFor={`${id}-date`}>On</label>
              <input id={`${id}-date`} type="date" value={form.date} onChange={(e) => set({ date: e.target.value })} />
            </>
          )}
        </fieldset>

        <fieldset className="share-fieldset">
          <legend>Downloads</legend>
          <label className="check-row">
            <input
              type="checkbox"
              checked={form.allowDownload}
              onChange={(e) => set({ allowDownload: e.target.checked, allowOriginal: e.target.checked && form.allowOriginal })}
            />
            Let visitors download
          </label>
          <label className={`check-row${form.allowDownload ? '' : ' disabled'}`}>
            <input
              type="checkbox"
              checked={form.allowDownload && form.allowOriginal}
              disabled={!form.allowDownload}
              onChange={(e) => set({ allowOriginal: e.target.checked })}
              aria-describedby={`${id}-orig-hint`}
            />
            Original files (full size, with their metadata)
          </label>
          <span id={`${id}-orig-hint`} className="hint">
            {form.allowDownload ? 'Otherwise visitors get display copies.' : 'Turn on downloads first.'}
          </span>
        </fieldset>

        <div className="error" role="alert">
          {tried && errors.length ? errors.join(' ') : error}
        </div>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy}>
            {busy ? 'Making the link…' : 'Create link'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
