// F7 cutouts in the app: the dialog's host, the "Cut out subject" buttons
// (preview, phone item sheet, info panel, board inspector) and the
// selection bar's More entry. Each is one line in the shared component.

import { useQuery } from '@tanstack/react-query'
import { Scissors } from 'lucide-react'
import { lazy, Suspense, useSyncExternalStore } from 'react'
import { thumbUrl, type Item } from '../../api'
import { canCutOut, cutoutApi, cutoutInfo, cutoutRequest, openCutout, subscribeCutout } from '../../cutouts'
import { useCaps, useCurrentItems } from '../../data'
import { useAppState } from '../../state'
import type { BulkMenuItem } from '../BulkBar'

const CutoutDialog = lazy(() => import('./CutoutDialog'))

/** Mounted once (App): shows the dialog whoever asked for it. */
export function CutoutHost() {
  const req = useSyncExternalStore(subscribeCutout, cutoutRequest)
  if (!req) return null
  return (
    <Suspense fallback={null}>
      <CutoutDialog req={req} />
    </Suspense>
  )
}

function useCutoutsOn(): boolean {
  const caps = useCaps()
  const st = useQuery({ queryKey: ['cutout-status'], queryFn: cutoutApi.status, staleTime: 60_000, enabled: caps.canEdit, retry: false })
  return caps.canEdit && !!st.data?.enabled
}

/** A "Cut out" button for one item (the preview's caption row, the phone
 * item sheet); nothing when the item can't be cut out. */
export function CutoutButton({ item, className = '', label = 'Cut out' }: { item: Item; className?: string; label?: string }) {
  const on = useCutoutsOn()
  if (!on || !canCutOut(item)) return null
  return (
    <button type="button" className={`btn-icon cutout-btn ${className}`.trim()} onClick={() => openCutout({ items: [item] })} title="Cut the subject out of its background: a new transparent image">
      <Scissors size={14} aria-hidden="true" /> {label}
    </button>
  )
}

const MODEL_LABEL: Record<string, string> = { 'isnet-general-use': 'Fast', 'birefnet-lite': 'Fine edges' }

/** The info panel's section: where a cut-out came from, or this image's
 * cut-outs and the button to make one. */
export function ItemCutouts({ item }: { item: Item }) {
  const on = useCutoutsOn()
  const { selectOnly } = useAppState()
  const info = cutoutInfo(item)
  const list = useQuery({ queryKey: ['cutouts', item.id], queryFn: () => cutoutApi.list(item.id), staleTime: 15_000, enabled: !info && item.kind === 'image' })
  if (info) {
    const how = [MODEL_LABEL[info.model] ?? info.model, info.feather ? `feather ${info.feather} px` : null, info.shadow ? 'shadow kept' : null].filter(Boolean).join(' · ')
    return (
      <div className="detail-section cutout-section">
        <h3 className="section-label">Cut-out</h3>
        <p className="cutout-from">
          Cut out from{' '}
          <button type="button" className="link-btn" onClick={() => selectOnly(info.source_item)}>
            the original
          </button>
          <span className="cutout-how"> · {how}</span>
        </p>
      </div>
    )
  }
  const cuts = list.data ?? []
  if (!cuts.length && !(on && canCutOut(item))) return null
  return (
    <div className="detail-section cutout-section">
      <div className="section-head-row">
        <h3 className="section-label">Cut-outs{cuts.length ? ` (${cuts.length})` : ''}</h3>
        {on && canCutOut(item) && (
          <button type="button" className="small btn-icon" onClick={() => openCutout({ items: [item] })}>
            <Scissors size={14} aria-hidden="true" /> Cut out subject…
          </button>
        )}
      </div>
      {cuts.length > 0 && (
        <ul className="cutout-list">
          {cuts.map((c) => {
            const t = thumbUrl(c, 256)
            return (
              <li key={c.id}>
                <button type="button" className="cutout-thumb" onClick={() => selectOnly(c.id)} title={c.filename} aria-label={`Open the cut-out ${c.filename}`}>
                  {t ? <img src={t} alt="" loading="lazy" /> : <Scissors size={16} aria-hidden="true" />}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** The board inspector's "Cut out" for one placed image. */
export function BoardCutoutButton({ boardId, nodeId, item }: { boardId: string; nodeId: string; item: Item | undefined }) {
  const on = useCutoutsOn()
  if (!on || !item || !canCutOut(item)) return null
  return (
    <button type="button" className="btn-icon cutout-btn" onClick={() => openCutout({ items: [item], board: { boardId, nodeId } })} title="Replace this image with its subject on a transparent background (undoable)">
      <Scissors size={14} aria-hidden="true" /> Cut out
    </button>
  )
}

/** The selection bar's More menu entry. */
export function useBulkCutoutMore(ids: string[], busy: boolean): BulkMenuItem[] {
  const on = useCutoutsOn()
  const { items } = useCurrentItems()
  const images = items.filter((i) => ids.includes(i.id) && canCutOut(i))
  if (!on) return []
  return [
    {
      key: 'cutout',
      label: images.length === ids.length ? 'Cut out subjects…' : `Cut out subjects (${images.length} images)…`,
      icon: Scissors,
      disabled: busy || images.length === 0,
      title: 'A transparent cut-out of each image, as new items',
      run: () => openCutout({ items: images }),
    },
  ]
}
