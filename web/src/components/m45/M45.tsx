// Milestones 4 and 5 dialogs that open from anywhere (the top bar, the
// selection bar, a board, the info panel, the preview): share, export,
// export images, clip, page snapshot. One context, lazily loaded dialogs,
// so the first page carries none of them.

import { createContext, lazy, Suspense, useContext, useMemo, useState, type ReactNode } from 'react'
import type { Item } from '../../api'
import type { ShareTargetKind } from '../../m45/share'

const ShareDialog = lazy(() => import('./ShareDialog'))
const ExportDialog = lazy(() => import('./ExportDialog'))
const ExportImagesDialog = lazy(() => import('./ExportImagesDialog'))
const ClipDialog = lazy(() => import('./ClipDialog'))
const SnapshotViewer = lazy(() => import('./SnapshotViewer'))

export interface Target {
  kind: ShareTargetKind | 'all'
  id: string | null
  itemIds: string[]
  /** what it is called ("Kyoto", "3 items") */
  name: string
}

export interface ClipRequest {
  item: Item
  start: number
  end: number
  /** the player, so in/out can come from its current time */
  player?: () => HTMLMediaElement | null
}

interface M45Api {
  openShare: (t: Target) => void
  openExport: (t: Target) => void
  openExportImages: (ids: string[]) => void
  openClip: (c: ClipRequest) => void
  openSnapshot: (item: Item) => void
}

const Ctx = createContext<M45Api | null>(null)

export function useM45(): M45Api {
  const c = useContext(Ctx)
  if (!c) throw new Error('useM45 outside M45Provider')
  return c
}

export function M45Provider({ children }: { children: ReactNode }) {
  const [share, setShare] = useState<Target | null>(null)
  const [exp, setExp] = useState<Target | null>(null)
  const [images, setImages] = useState<string[] | null>(null)
  const [clip, setClip] = useState<ClipRequest | null>(null)
  const [snapshot, setSnapshot] = useState<Item | null>(null)
  const api = useMemo<M45Api>(
    () => ({
      openShare: setShare,
      openExport: setExp,
      openExportImages: (ids) => ids.length && setImages(ids),
      openClip: setClip,
      openSnapshot: setSnapshot,
    }),
    [],
  )
  return (
    <Ctx.Provider value={api}>
      {children}
      <Suspense fallback={null}>
        {share && share.kind !== 'all' && <ShareDialog target={share} onClose={() => setShare(null)} />}
        {exp && <ExportDialog target={exp} onClose={() => setExp(null)} />}
        {images && <ExportImagesDialog ids={images} onClose={() => setImages(null)} />}
        {clip && <ClipDialog req={clip} onClose={() => setClip(null)} />}
        {snapshot && <SnapshotViewer item={snapshot} onClose={() => setSnapshot(null)} />}
      </Suspense>
    </Ctx.Provider>
  )
}
