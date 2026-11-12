// The selection bar's Milestone 4 actions, in its More menu (B5): share
// the selection as a link, export it (BagIt zip), export resized copies
// or a contact sheet.

import { Download, Images, Share2 } from 'lucide-react'
import type { BulkMenuItem } from '../BulkBar'
import { useM45 } from './M45'

export function useBulkM45More(ids: string[], busy: boolean): BulkMenuItem[] {
  const { openShare, openExport, openExportImages } = useM45()
  const name = `${ids.length} item${ids.length === 1 ? '' : 's'}`
  const t = { kind: 'items' as const, id: null, itemIds: ids, name }
  return [
    { key: 'share', label: 'Share link…', icon: Share2, disabled: busy, title: 'A read-only link to these items', run: () => openShare(t) },
    { key: 'export', label: 'Export…', icon: Download, disabled: busy, title: 'A zip with the originals, sidecars and an offline index', run: () => openExport(t) },
    { key: 'export-images', label: 'Export images…', icon: Images, disabled: busy, title: 'Resized copies, a watermark or a contact sheet', run: () => openExportImages(ids) },
  ]
}
