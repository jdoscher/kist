// The selection bar's Milestone 4 actions: share the selection as a link,
// export it (BagIt zip), export resized copies or a contact sheet.

import { Download, Images, Share2 } from 'lucide-react'
import { useM45 } from './M45'

export function BulkM45({ ids, busy }: { ids: string[]; busy: boolean }) {
  const { openShare, openExport, openExportImages } = useM45()
  const name = `${ids.length} item${ids.length === 1 ? '' : 's'}`
  const t = { kind: 'items' as const, id: null, itemIds: ids, name }
  return (
    <>
      <span className="bulk-sep" aria-hidden="true" />
      <button className="btn-icon" disabled={busy} onClick={() => openShare(t)} title="A read-only link to these items">
        <Share2 size={14} strokeWidth={1.75} aria-hidden="true" /> Share
      </button>
      <button className="btn-icon" disabled={busy} onClick={() => openExport(t)} title="A zip with the originals, sidecars and an offline index">
        <Download size={14} strokeWidth={1.75} aria-hidden="true" /> Export
      </button>
      <button className="btn-icon" disabled={busy} onClick={() => openExportImages(ids)} title="Resized copies, a watermark or a contact sheet">
        <Images size={14} strokeWidth={1.75} aria-hidden="true" /> Export images
      </button>
    </>
  )
}
