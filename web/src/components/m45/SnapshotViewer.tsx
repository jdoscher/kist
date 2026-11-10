// The page an item came from, as saved (Milestone 4): the snapshot in a
// fully sandboxed frame (no script, no network: the server's CSP is
// sandbox too), or its readable text.

import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { useId, useState } from 'react'
import type { Item } from '../../api'
import { api45 } from '../../m45/api45'
import { linkableUrl } from '../../format'
import { describeError } from '../../writes'
import { Modal } from '../Dialog'

export default function SnapshotViewer({ item, onClose }: { item: Item; onClose: () => void }) {
  const [tab, setTab] = useState<'page' | 'text'>('page')
  const id = useId()
  const text = useQuery({ queryKey: ['snapshot-text', item.id], queryFn: () => api45.snapshotText(item.id), retry: false })
  const page = linkableUrl((item.provenance?.page_url as string | undefined) ?? item.source_url)
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      const next = tab === 'page' ? 'text' : 'page'
      setTab(next)
      document.getElementById(`${id}-tab-${next}`)?.focus()
    }
  }
  return (
    <Modal title="Saved page" onClose={onClose} className="dialog snapshot-dialog">
      <div className="snapshot-head">
        <div role="tablist" aria-label="Show" className="seg" onKeyDown={onKey}>
          {(['page', 'text'] as const).map((t) => (
            <button
              key={t}
              id={`${id}-tab-${t}`}
              role="tab"
              type="button"
              aria-selected={tab === t}
              aria-controls={`${id}-panel-${t}`}
              tabIndex={tab === t ? 0 : -1}
              className={tab === t ? 'on' : ''}
              onClick={() => setTab(t)}
              data-autofocus={tab === t ? '' : undefined}
            >
              {t === 'page' ? 'Page' : 'Text'}
            </button>
          ))}
        </div>
        {page && (
          <a href={page.href} target="_blank" rel="noreferrer noopener" className="btn-icon">
            Live page on {page.host} <ExternalLink size={13} strokeWidth={1.75} aria-hidden="true" />
            <span className="sr-only"> (new tab)</span>
          </a>
        )}
      </div>
      <p className="hint">A copy kept when the item was saved, with scripts removed. Links in it don’t work here.</p>
      {tab === 'page' ? (
        <div id={`${id}-panel-page`} role="tabpanel" aria-labelledby={`${id}-tab-page`} className="snapshot-frame-wrap">
          <iframe
            className="snapshot-frame"
            src={`/api/items/${item.id}/snapshot`}
            title={`Saved copy of the page ${item.filename} came from`}
            sandbox=""
            referrerPolicy="no-referrer"
          />
        </div>
      ) : (
        <div id={`${id}-panel-text`} role="tabpanel" aria-labelledby={`${id}-tab-text`} className="snapshot-text" tabIndex={0}>
          {text.isLoading ? 'Loading…' : text.isError ? describeError(text.error) : text.data || 'No readable text.'}
        </div>
      )}
    </Modal>
  )
}
