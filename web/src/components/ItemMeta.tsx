import { ExternalLink } from 'lucide-react'
import type { Item } from '../api'
import { fmtBytes, fmtDuration, linkableUrl } from '../format'

/** Read-only facts about one item (desktop panel and phone sheet). */
export function ItemMeta({ item }: { item: Item }) {
  const source = linkableUrl(item.source_url)
  return (
    <>
      <div className="detail-section meta-list">
        <div><span>Type</span><span className="mono">{item.mime}</span></div>
        <div><span>Size</span><span>{fmtBytes(item.size_bytes)}</span></div>
        {item.width != null && (
          <div><span>Dimensions</span><span className="mono">{item.width}×{item.height}</span></div>
        )}
        {item.duration_s != null && (
          <div><span>Duration</span><span>{fmtDuration(item.duration_s)}</span></div>
        )}
        <div><span>Added</span><span>{new Date(item.created_at).toLocaleString()}</span></div>
        {item.source_url && (
          <div>
            <span>Source</span>
            {source ? (
              <a className="mono" href={source.href} target="_blank" rel="noreferrer">
                {source.host} <ExternalLink size={14} aria-hidden="true" />
              </a>
            ) : (
              // not an http(s) URL: show it, don't link it (and never crash)
              <span className="source-text mono">{item.source_url}</span>
            )}
          </div>
        )}
      </div>

      {/* the colours live in the Colours strip (PaletteSwatches), once */}
    </>
  )
}
