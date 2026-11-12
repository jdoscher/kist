// /timeline (Milestone 5): the library by date taken (else the date
// added), as days, months or years with counts; picking one shows those
// items in the grid. On this day sits on top.

import { useQuery } from '@tanstack/react-query'
import { CalendarDays } from 'lucide-react'
import { useId, useMemo } from 'react'
import type { Item } from '../../api'
import { thumbUrl } from '../../api'
import { api45 } from '../../m45/api45'
import { barPct, bucketLabel, bucketRange, groupBuckets, localDay, yearsAgoText, type Bucket, type TimelineGroup } from '../../m45/timeline'
import { useAppState } from '../../state'
import { describeError } from '../../writes'

const GROUPS: [TimelineGroup, string][] = [
  ['day', 'Days'],
  ['month', 'Months'],
  ['year', 'Years'],
]

/** Items taken on this date in earlier years. */
export function OnThisDayCard({ compact = false }: { compact?: boolean }) {
  const { selectOnly, setPreviewId } = useAppState()
  const day = localDay()
  const otd = useQuery({ queryKey: ['on-this-day', day], queryFn: () => api45.onThisDay(day), staleTime: 3_600_000 })
  const id = useId()
  if (!otd.data || otd.data.years.length === 0) return null
  const open = (it: Item) => {
    selectOnly(it.id)
    setPreviewId(it.id)
  }
  return (
    <section className={`otd-card${compact ? ' compact' : ''}`} aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="section-label">
        <CalendarDays size={14} aria-hidden="true" /> On this day
      </h2>
      {otd.data.years.map((y) => (
        <div key={y.year} className="otd-year">
          <h3 className="otd-when">
            {yearsAgoText(y.years_ago)} <span className="hint">{y.year}</span>
          </h3>
          <ul className="otd-items">
            {y.items.slice(0, compact ? 4 : 8).map((it) => {
              const t = thumbUrl(it, 256)
              return (
                <li key={it.id}>
                  <button type="button" onClick={() => open(it)} aria-label={`Open ${it.filename}`} title={it.filename}>
                    {t ? <img src={t} alt="" loading="lazy" /> : <span className="otd-ext">.{it.ext}</span>}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </section>
  )
}

export default function TimelineScreen({ group }: { group: TimelineGroup }) {
  const { query, setQuery, openScreen } = useAppState()
  const tl = useQuery({ queryKey: ['timeline', group, query], queryFn: () => api45.timeline(group, query) })
  const id = useId()
  const sections = useMemo(() => groupBuckets(tl.data ?? []), [tl.data])
  const max = Math.max(1, ...(tl.data ?? []).map((b) => b.count))
  const total = (tl.data ?? []).reduce((a, b) => a + b.count, 0)

  const jump = (b: Bucket) => {
    const r = bucketRange(b.key)
    if (!r) return
    // the timeline's date: taken, else added (date_after/date_before)
    setQuery({ date_after: `${r.after}T00:00:00Z`, date_before: `${r.before}T23:59:59Z`, order: '-taken' })
  }

  return (
    <main className="grid-area timeline-screen" aria-labelledby={`${id}-h`}>
      <header className="screen-head">
        <h1 id={`${id}-h`} className="view-title">Timeline</h1>
        <div className="seg" role="group" aria-label="Group by">
          {GROUPS.map(([g, label]) => (
            <button key={g} type="button" aria-pressed={group === g} className={group === g ? 'on' : ''} onClick={() => openScreen({ kind: 'timeline', group: g })}>
              {label}
            </button>
          ))}
        </div>
        <p className="hint">
          {tl.data ? `${total.toLocaleString()} items by the date they were taken; items without one count on the day they were added.` : ''}
        </p>
      </header>
      <div className="timeline-scroll">
        <OnThisDayCard />
        {sections.length > 1 && group !== 'year' && (
          <nav className="timeline-jump" aria-label="Jump to">
            {sections.map((s) => (
              <a key={s.section} href={`#tl-${s.section.replace(/\s+/g, '-')}`}>
                {s.section}
              </a>
            ))}
          </nav>
        )}
        {tl.isLoading && <p className="hint" role="status">Loading…</p>}
        {tl.isError && <p role="alert">Couldn’t load the timeline: {describeError(tl.error)}</p>}
        {tl.data && tl.data.length === 0 && <p className="hint">Nothing here yet.</p>}
        {sections.map((s) => (
          <section key={s.section || 'all'} id={s.section ? `tl-${s.section.replace(/\s+/g, '-')}` : undefined} className="timeline-section" aria-label={s.section || 'Years'}>
            {s.section && (
              <h2 className="timeline-section-h view-title">
                {s.section} <span className="title-count">{s.total.toLocaleString()}</span>
              </h2>
            )}
            <ul className="timeline-list">
              {s.buckets.map((b) => (
                <li key={b.key}>
                  <button type="button" className="timeline-row" onClick={() => jump(b)}>
                    {b.first_item_id ? <img src={`/api/items/${b.first_item_id}/thumb?size=256`} alt="" loading="lazy" /> : <span className="tl-noimg" />}
                    <span className="tl-label">{bucketLabel(b.key)}</span>
                    <span className="tl-bar" aria-hidden="true">
                      <span style={{ width: `${barPct(b.count, max)}%` }} />
                    </span>
                    <span className="tl-count count">
                      {b.count.toLocaleString()}
                      <span className="sr-only"> item{b.count === 1 ? '' : 's'}, show them</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </main>
  )
}
