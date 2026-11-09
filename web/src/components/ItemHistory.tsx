import { ChevronRight, RotateCcw } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Item, type ItemLink } from '../api'
import { useAfterUndo, useCaps, useFolderNames, useMe } from '../data'
import { canRevert, describeEvent, sourceText, timeAgo, undoReport, type Names } from '../history'
import { useWrite } from '../useWrite'
import { useDialog } from './Dialog'
import { useNotices } from './Notices'

/** A collapsible section: the heading is a button that shows and hides it
 * (closed by default, so the history is only fetched when wanted). */
export function Collapsible({
  title,
  count,
  children,
  defaultOpen = false,
  onToggle,
}: {
  title: string
  count?: number
  children: React.ReactNode
  defaultOpen?: boolean
  onToggle?: (open: boolean) => void
}) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  return (
    <section className="detail-section collapsible" aria-labelledby={`${id}-h`}>
      <h3 className="section-label" id={`${id}-h`}>
        <button
          className="collapse-btn"
          aria-expanded={open}
          aria-controls={`${id}-body`}
          onClick={() => {
            setOpen(!open)
            onToggle?.(!open)
          }}
        >
          <ChevronRight size={14} strokeWidth={2} aria-hidden="true" className={open ? 'open' : ''} />
          {title}
          {count !== undefined && count > 0 && <span className="count"> ({count})</span>}
        </button>
      </h3>
      <div id={`${id}-body`} hidden={!open}>
        {open && children}
      </div>
    </section>
  )
}

/** Names for history sentences: folders by id, linked items by id. */
export function useHistoryNames(itemId: string): Names {
  const folderName = useFolderNames()
  const { data: me } = useMe()
  const qc = useQueryClient()
  const links = qc.getQueryData<ItemLink[]>(['links', itemId])
  return useMemo(() => {
    const items = new Map((links ?? []).map((l) => [l.item.id, l.item.filename]))
    return { folder: (id) => folderName.get(id), item: (id) => items.get(id), self: me?.name }
  }, [folderName, links, me?.name])
}

function HistoryList({ item }: { item: Item }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['history', item.id],
    queryFn: () => api.history(item.id),
  })
  const names = useHistoryNames(item.id)
  const caps = useCaps()
  const dialog = useDialog()
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  const w = useWrite()

  const revert = async (eventId: number, sentence: string) => {
    const ok = await dialog.confirm({
      title: 'Revert this change?',
      message: `“${sentence}” will be undone. If something changed the same thing since, nothing is overwritten and you’ll be told why.`,
      confirmLabel: 'Revert',
    })
    if (!ok) return
    void w.run(`revert:${eventId}`, () => api.revertEvent(eventId), {
      what: 'revert the change',
      onDone: (r) => {
        afterUndo()
        const rep = undoReport(r, 'Reverted', names)
        notify({ key: `revert:${item.id}`, tone: rep.tone, message: rep.message })
      },
    })
  }

  if (isLoading) return <p className="hint" role="status">Loading history…</p>
  if (isError) return <p className="hint" role="alert">Couldn’t load the history: {String((error as Error).message)}</p>
  if (!data?.length) return <p className="hint">No changes recorded yet.</p>
  const now = Date.now()
  return (
    <ol className="history-list">
      {data.map((ev) => {
        const sentence = describeEvent(ev, names)
        const src = sourceText(ev.source, ev.actor)
        return (
          <li key={ev.id} className="history-row">
            <div className="history-text">
              <span className="history-what">{sentence}</span>
              <span className="history-when">
                {src && `${src} · `}
                <time dateTime={ev.at} title={new Date(ev.at).toLocaleString()}>
                  {timeAgo(ev.at, now)}
                </time>
              </span>
            </div>
            {caps.canEdit && canRevert(ev) && (
              <button
                className="icon-btn"
                disabled={w.busy(`revert:${ev.id}`)}
                onClick={() => void revert(ev.id, sentence)}
                aria-label={`Revert: ${sentence}`}
                title="Revert this change"
              >
                <RotateCcw size={14} strokeWidth={1.75} aria-hidden="true" />
              </button>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** Every change to the item: who, from where, when and what, newest
 * first, each with Revert (desktop info panel and phone sheet). */
export function ItemHistory({ item }: { item: Item }) {
  return (
    <Collapsible title="History">
      <HistoryList item={item} />
    </Collapsible>
  )
}
