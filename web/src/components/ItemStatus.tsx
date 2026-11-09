import { api, type Item } from '../api'
import { useItemWrite } from '../useWrite'

/** The item's server-side states, with the action that moves each one on:
 * Trash -> Restore, failed preview -> Retry. Shown on desktop and phone.
 * A pending preview is re-checked by useItem (data.ts), not here. */
export function ItemStatus({ item }: { item: Item }) {
  const { write, busy } = useItemWrite(item.id)

  // always mounted, so changes (Retry -> making the preview -> done) are read out
  const quiet = item.state === 'active' && (item.preview_status === 'ready' || item.preview_status === 'none')
  if (quiet) return <div className="item-status empty" aria-live="polite" />
  return (
    <div className="item-status" aria-live="polite">
      {item.state === 'trashed' && (
        <div className="status-row trashed">
          <span>In Trash</span>
          <button disabled={busy()} onClick={() => void write('restore', () => api.restoreItem(item.id))}>
            Restore
          </button>
        </div>
      )}
      {item.preview_status === 'failed' && (
        <div className="status-row failed">
          <span title={item.preview_error ?? ''}>
            Preview failed{item.preview_error ? `: ${item.preview_error}` : ''}
          </span>
          <button disabled={busy()} onClick={() => void write('retry', () => api.retryPreview(item.id))}>
            Retry
          </button>
        </div>
      )}
      {item.preview_status === 'pending' && (
        <div className="status-row pending">
          <span>Making the preview…</span>
        </div>
      )}
    </div>
  )
}
