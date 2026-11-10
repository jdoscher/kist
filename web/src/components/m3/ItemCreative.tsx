// The info panel's Milestone 3 section: flag and label, versions (the
// version_of chain, upload a new one, compare with the previous), and the
// boards the item is on.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Columns2, Layers, LayoutDashboard, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { thumbUrl, type Item } from '../../api'
import { sheetCompareChoices } from '../../compareTargets'
import { api3, uploadVersion } from '../../creativeApi'
import { org } from '../../organiseApi'
import { useApplyItems, useCaps, useRefresh } from '../../data'
import { afterVersionUpload } from '../../versions'
import { describeError } from '../../writes'
import { useAppState } from '../../state'
import { useNotices } from '../Notices'
import { useCreative } from './Creative'
import { FlagButtons, LabelPicker } from './CullControls'
import { usePatchItem } from './writes3'

export function ItemCull({ item }: { item: Item }) {
  const { patch, busy } = usePatchItem()
  return (
    <div className="detail-section cull-section">
      <h3 className="section-label">Cull</h3>
      <FlagButtons flag={item.flag} disabled={busy(`${item.id}:flag`)} onFlag={(flag) => void patch(item, { flag })} />
      <LabelPicker label={item.label} disabled={busy(`${item.id}:label`)} onLabel={(label) => void patch(item, { label })} />
    </div>
  )
}

const dateText = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })

export function ItemVersions({ item }: { item: Item }) {
  const caps = useCaps()
  const { openCompare } = useCreative()
  const { selectOnly } = useAppState()
  const qc = useQueryClient()
  const apply = useApplyItems()
  const refresh = useRefresh()
  const { notify } = useNotices()
  const fileRef = useRef<HTMLInputElement>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const versions = useQuery({ queryKey: ['versions', item.id], queryFn: () => api3.versions(item.id), staleTime: 10_000 })
  const chain = versions.data?.items ?? []
  const at = chain.findIndex((v) => v.id === item.id)
  const prev = at > 0 ? chain[at - 1] : null

  const upload = async (file: File) => {
    setProgress(0)
    try {
      const r = await uploadVersion(item.id, file, setProgress)
      apply([r.item])
      refresh.itemsAdded()
      const next = afterVersionUpload(r, item.id)
      for (const key of next.invalidate) void qc.invalidateQueries({ queryKey: [key] })
      // the info panel moves to the new version (the stack's cover now)
      selectOnly(next.select)
      const compare = next.compare
      notify({ key: 'version', message: next.message, action: compare ? { label: 'Compare', run: () => openCompare(compare) } : undefined })
    } catch (e) {
      notify({ key: 'version', tone: 'error', message: `Couldn't add the version: ${describeError(e)}` })
    } finally {
      setProgress(null)
    }
  }

  if (chain.length <= 1 && !caps.canEdit) return null
  return (
    <div className="detail-section versions-section">
      <div className="section-head-row">
        <h3 className="section-label">Versions{chain.length > 1 ? ` (${chain.length})` : ''}</h3>
        {caps.canEdit && (
          <button className="small btn-icon" onClick={() => fileRef.current?.click()} disabled={progress !== null}>
            <Upload size={13} strokeWidth={1.75} aria-hidden="true" />
            New version
          </button>
        )}
      </div>
      <input
        ref={fileRef}
        type="file"
        hidden
        aria-hidden="true"
        tabIndex={-1}
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) void upload(f)
        }}
      />
      {progress !== null && <progress max={100} value={progress} aria-label="Uploading the new version" />}
      {chain.length > 1 ? (
        <VersionList chain={chain} itemId={item.id} currentId={versions.data?.current_id} onOpen={selectOnly} />
      ) : (
        <p className="hint">One version. Upload a new one to keep both, linked, with the newest shown as current.</p>
      )}
      {prev && (
        <button className="btn-icon" onClick={() => openCompare([prev.id, item.id])}>
          <Columns2 size={14} strokeWidth={1.75} aria-hidden="true" />
          Compare with v{at}
        </button>
      )}
    </div>
  )
}

function VersionList({ chain, itemId, currentId, onOpen }: { chain: Item[]; itemId: string; currentId?: string | null; onOpen: (id: string) => void }) {
  return (
    <ol className="version-list">
      {chain.map((v, i) => (
        <li key={v.id} className={v.id === itemId ? 'here' : ''}>
          <button className="link-open" onClick={() => onOpen(v.id)} aria-current={v.id === itemId ? 'true' : undefined}>
            {thumbUrl(v) ? <img className="link-thumb" src={thumbUrl(v) ?? ''} alt="" /> : <span className="link-thumb icon" aria-hidden="true"><Layers size={14} /></span>}
            <span className="link-text">
              <span className="link-kind">
                v{i + 1}
                {v.id === currentId ? ' · current' : ''}
              </span>
              <span className="link-name">{v.filename}</span>
              <span className="version-date">{dateText(v.created_at)}</span>
            </span>
          </button>
        </li>
      ))}
    </ol>
  )
}

/** The phone sheet's Versions and Compare: the version chain (tap one to
 * open it), "Compare with vN" like the info panel, and "Compare with
 * stack" for a stacked item. Read-only: no upload here. Compare opens in
 * its phone layout. */
export function SheetVersions({ item }: { item: Item }) {
  const { openCompare } = useCreative()
  const { selectOnly } = useAppState()
  const versions = useQuery({ queryKey: ['versions', item.id], queryFn: () => api3.versions(item.id), staleTime: 10_000 })
  const stack = useQuery({
    queryKey: ['stack', item.stack_id],
    queryFn: () => org.stack(item.stack_id as string),
    enabled: !!item.stack_id,
    retry: false,
  })
  const chain = versions.data?.items ?? []
  const choices = sheetCompareChoices(chain, item.stack_id ? stack.data : null, item.id)
  if (chain.length <= 1 && !choices.length) return null
  return (
    <div className="detail-section versions-section sheet-versions">
      <h3 className="section-label">{chain.length > 1 ? `Versions (${chain.length})` : 'Compare'}</h3>
      {chain.length > 1 && <VersionList chain={chain} itemId={item.id} currentId={versions.data?.current_id} onOpen={selectOnly} />}
      <div className="btn-row">
        {choices.map((c) => (
          <button key={c.key} className="btn-icon" onClick={() => openCompare(c.ids)}>
            <Columns2 size={14} strokeWidth={1.75} aria-hidden="true" />
            {c.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export function ItemBoards({ item }: { item: Item }) {
  const { openScreen } = useAppState()
  const boards = useQuery({ queryKey: ['itemBoards', item.id], queryFn: () => api3.itemBoards(item.id), staleTime: 30_000 })
  if (!boards.data?.length) return null
  return (
    <div className="detail-section">
      <h3 className="section-label">On boards</h3>
      <ul className="board-chips">
        {boards.data.map((b) => (
          <li key={b.id}>
            <button className="chip btn-icon" onClick={() => openScreen({ kind: 'board', id: b.id })}>
              <LayoutDashboard size={12} strokeWidth={1.75} aria-hidden="true" />
              {b.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
