// Organise: the screens for keeping a growing library tidy, each with its
// own URL (/organise/<section>) so Back works. Duplicates review, stack
// suggestions, suggested collections ("You seem to collect…") and people.
// Every change is one batch; the notice after it has Undo.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, Layers, Library, RefreshCw, Users, X } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { api, emptyQuery, newBatchId, type Item } from '../api'
import { useAfterUndo, useCaps, useRefresh } from '../data'
import { fmtBytes, plural } from '../format'
import { timeAgo } from '../history'
import { org, type Cluster, type DuplicateGroup, type DuplicateKind, type MergeOptions, type Person, type StackSuggestion } from '../organiseApi'
import { useAppState } from '../state'
import type { OrganiseSection } from '../url'
import { splitTag } from '../tagNs'
import { describeError } from '../writes'
import { Mark, Wordmark } from './Brand'
import { useDialog } from './Dialog'
import { useAfterStackChange } from './GridExtras'
import { TagText, useAiFlags } from './ItemAI'
import { useNotices } from './Notices'

const SECTIONS: { id: OrganiseSection; label: string; icon: typeof Copy }[] = [
  { id: 'duplicates', label: 'Duplicates', icon: Copy },
  { id: 'stacks', label: 'Stacks', icon: Layers },
  { id: 'collections', label: 'Collections', icon: Library },
  { id: 'people', label: 'People', icon: Users },
]

const thumbOf = (id: string, size = 256) => `/api/items/${encodeURIComponent(id)}/thumb?size=${size}`

/** Dismissed suggestions, kept per browser (the server has no dismiss). */
function useDismissed(name: string): [Set<string>, (id: string) => void] {
  const key = `kist.dismissed.${name}`
  const [set, setSet] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(key) ?? '[]') as string[])
    } catch {
      return new Set()
    }
  })
  const add = (id: string) =>
    setSet((s) => {
      const next = new Set(s).add(id)
      try {
        localStorage.setItem(key, JSON.stringify([...next].slice(-500)))
      } catch {
        /* private mode */
      }
      return next
    })
  return [set, add]
}

/** Run a change as one batch, then offer Undo. */
function useBatchRun() {
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  return async (
    what: { done: (r: unknown) => string; fail: string; key: string },
    fn: (batch: string) => Promise<unknown>,
    after: () => void,
  ): Promise<boolean> => {
    const batch = newBatchId()
    try {
      const r = await fn(batch)
      after()
      notify({
        key: what.key,
        message: what.done(r),
        action: {
          label: 'Undo',
          run: () =>
            void api.undoBatch(batch).then(
              () => {
                afterUndo()
                after()
                notify({ key: what.key, message: 'Undone.' })
              },
              (e) => notify({ key: what.key, tone: 'error', message: `Couldn't undo: ${describeError(e)}` }),
            ),
        },
      })
      return true
    } catch (e) {
      notify({ key: what.key, tone: 'error', message: `${what.fail}: ${describeError(e)}` })
      return false
    }
  }
}

export function OrganiseScreen() {
  const { organise, openOrganise, closeOrganise, mobile } = useAppState()
  const caps = useCaps()
  const section = organise ?? 'duplicates'
  const label = SECTIONS.find((s) => s.id === section)?.label ?? ''
  return (
    <div className={`settings-screen organise-screen${mobile ? ' mobile' : ''}`}>
      <header className="settings-head">
        <div className="brand" title="Kist">
          <Mark />
          <Wordmark />
        </div>
        <h1 className="settings-title">
          Organise<span className="sr-only">: {label}</span>
        </h1>
        <button className="btn-icon settings-close" onClick={closeOrganise}>
          <X size={16} strokeWidth={1.75} aria-hidden="true" />
          Back to library
        </button>
      </header>
      {caps.restricted ? (
        <main className="settings-body">
          <p>Organising needs a token that can see the whole library.</p>
        </main>
      ) : (
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="Organise sections">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                className={section === s.id ? 'on' : ''}
                aria-current={section === s.id ? 'page' : undefined}
                onClick={() => openOrganise(s.id)}
              >
                <s.icon size={16} strokeWidth={1.75} aria-hidden="true" />
                {s.label}
              </button>
            ))}
          </nav>
          <main className="settings-body organise-body">
            {section === 'duplicates' && <DuplicatesSection />}
            {section === 'stacks' && <StacksSection />}
            {section === 'collections' && <CollectionsSection />}
            {section === 'people' && <PeopleSection />}
          </main>
        </div>
      )}
    </div>
  )
}

// ---------- duplicates ----------

const DUP_KINDS: { id: DuplicateKind; label: string; hint: string }[] = [
  { id: 'same_image', label: 'Same picture', hint: 'The same image saved more than once (resized, re-encoded, screenshotted)' },
  { id: 'similar', label: 'Look alike', hint: 'Very close but not the same: bursts, edits, crops' },
]

function DuplicatesSection() {
  const [kind, setKind] = useState<DuplicateKind>('same_image')
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['dupGroups', kind],
    queryFn: () => org.duplicateGroups(kind),
    retry: false,
  })
  const qc = useQueryClient()
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['dupGroups'] })
    void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
    void qc.invalidateQueries({ queryKey: ['stats'] })
  }
  const groups = data ?? []
  const caps = useCaps()
  const dialog = useDialog()
  const run = useBatchRun()
  const id = useId()

  const resolveAll = async () => {
    const ok = await dialog.confirm({
      title: `Keep the suggested copy in all ${groups.length} groups?`,
      message:
        'In each group the suggested keeper (largest, then oldest, then the one with a source) gets the others’ tags, folders, notes, best rating and source, and the others go to the Trash. One Undo puts everything back.',
      confirmLabel: 'Keep the suggestions',
    })
    if (!ok) return
    await run(
      { key: 'dups', fail: "Couldn't resolve them", done: () => `Resolved ${plural(groups.length, 'group')}; the extra copies are in the Trash.` },
      async (batch) => {
        for (const g of groups)
          await org.resolveDuplicates(
            g.suggested_keep,
            g.items.map((i) => i.id).filter((x) => x !== g.suggested_keep),
            MERGE_ALL,
            batch,
          )
      },
      refresh,
    )
  }

  return (
    <section aria-labelledby={`${id}-h`}>
      <div className="settings-section-head">
        <h2 id={`${id}-h`}>Duplicates</h2>
        <button className="btn-icon" onClick={() => void refetch()} disabled={isFetching}>
          <RefreshCw size={14} strokeWidth={1.75} aria-hidden="true" className={isFetching ? 'spin' : ''} />
          Check again
        </button>
      </div>
      <p className="settings-intro">
        Pick the copy to keep. Its tags, folders, notes, best rating and source are kept, and the other copies go to the
        Trash (one Undo brings them back). Not the same thing? Say so and the pair won’t be shown again.
      </p>
      <div className="seg" role="radiogroup" aria-label="Kind of duplicate">
        {DUP_KINDS.map((k) => (
          <button key={k.id} role="radio" aria-checked={kind === k.id} className={kind === k.id ? 'on' : ''} onClick={() => setKind(k.id)} title={k.hint}>
            {k.label}
          </button>
        ))}
      </div>
      <p className="hint">{DUP_KINDS.find((k) => k.id === kind)?.hint}.</p>
      {isLoading && (
        <p className="hint" role="status">
          Looking for duplicates…
        </p>
      )}
      {isError && <p role="alert">Couldn’t look for duplicates: {describeError(error)}</p>}
      {data && groups.length === 0 && (
        <p className="empty-note" role="status">
          No {kind === 'same_image' ? 'duplicate pictures' : 'look-alikes'} found.
        </p>
      )}
      {groups.length > 1 && kind === 'same_image' && caps.canEdit && (
        <div className="bulk-line">
          <span>{plural(groups.length, 'group')}</span>
          <button onClick={() => void resolveAll()}>Keep the suggested copy in every group…</button>
        </div>
      )}
      <ul className="dup-groups">
        {groups.map((g) => (
          <DupGroup key={g.id} group={g} onDone={refresh} canEdit={caps.canEdit} />
        ))}
      </ul>
    </section>
  )
}

const MERGE_ALL: MergeOptions = { tags: true, folders: true, notes: true, rating: 'max', source_url: true }

function DupGroup({ group, onDone, canEdit }: { group: DuplicateGroup; onDone: () => void; canEdit: boolean }) {
  const [keep, setKeep] = useState(group.suggested_keep)
  const [merge, setMerge] = useState<MergeOptions>(MERGE_ALL)
  const [busy, setBusy] = useState(false)
  const { setPreviewId } = useAppState()
  const run = useBatchRun()
  const id = useId()
  const others = group.items.map((i) => i.id).filter((x) => x !== keep)
  const best = group.pairs.reduce((m, p) => Math.max(m, p.score), 0)

  const resolve = async () => {
    setBusy(true)
    await run(
      { key: 'dups', fail: "Couldn't resolve them", done: () => `Kept one; ${others.length === 1 ? '1 copy' : `${others.length} copies`} moved to the Trash.` },
      (batch) => org.resolveDuplicates(keep, others, merge, batch),
      onDone,
    )
    setBusy(false)
  }
  const notDup = async () => {
    setBusy(true)
    await run(
      { key: 'dups', fail: "Couldn't save that", done: () => 'Marked as not duplicates; they won’t be grouped again.' },
      async (batch) => {
        for (const p of group.pairs) await org.notDuplicate(p.a, p.b, batch)
      },
      onDone,
    )
    setBusy(false)
  }

  const opt = (k: keyof MergeOptions, label: string) => (
    <label className="check-row">
      <input
        type="checkbox"
        checked={k === 'rating' ? merge.rating === 'max' : !!merge[k]}
        onChange={(e) => setMerge((m) => ({ ...m, [k]: k === 'rating' ? (e.target.checked ? 'max' : 'keep') : e.target.checked }))}
      />
      {label}
    </label>
  )

  return (
    <li className="dup-group" aria-labelledby={`${id}-t`}>
      <h3 id={`${id}-t`} className="dup-title">
        {`${group.items.length} copies`}
        <span className="hint-inline">
          {group.kind === 'similar' ? ` · ${Math.round(best * 100)}% alike` : ' · same picture'}
        </span>
      </h3>
      <div className="dup-items" role="radiogroup" aria-label="Copy to keep">
        {group.items.map((it) => (
          <DupCard key={it.id} item={it} keep={keep === it.id} suggested={group.suggested_keep === it.id} onKeep={() => setKeep(it.id)} onOpen={() => setPreviewId(it.id)} name={`${id}-keep`} disabled={!canEdit} />
        ))}
      </div>
      {canEdit && (
        <>
          <details className="dup-options">
            <summary>Carry over to the copy you keep</summary>
            <div className="dup-option-list">
              {opt('tags', 'Tags')}
              {opt('folders', 'Folders')}
              {opt('notes', 'Notes')}
              {opt('rating', 'Best rating')}
              {opt('source_url', 'Source link')}
            </div>
          </details>
          <div className="btn-row">
            <button className="primary" disabled={busy} onClick={() => void resolve()}>
              Keep this one, trash {others.length === 1 ? 'the other' : `the other ${others.length}`}
            </button>
            <button disabled={busy} onClick={() => void notDup()}>
              Not duplicates
            </button>
          </div>
        </>
      )}
    </li>
  )
}

function DupCard({
  item,
  keep,
  suggested,
  onKeep,
  onOpen,
  name,
  disabled,
}: {
  item: Item
  keep: boolean
  suggested: boolean
  onKeep: () => void
  onOpen: () => void
  name: string
  disabled: boolean
}) {
  const id = useId()
  return (
    <div className={`dup-card${keep ? ' keep' : ''}`}>
      <button type="button" className="dup-thumb" onClick={onOpen} aria-label={`Open ${item.filename} full screen`}>
        {item.thumb_url ? <img src={thumbOf(item.id, 512)} alt="" loading="lazy" /> : <span>.{item.ext}</span>}
      </button>
      <label className="check-row keep-radio" htmlFor={`${id}-r`}>
        <input id={`${id}-r`} type="radio" name={name} checked={keep} onChange={onKeep} disabled={disabled} />
        {keep ? 'Keep' : 'Keep this'}
        {suggested && <span className="pill">suggested</span>}
      </label>
      <dl className="dup-facts">
        <div>
          <dt>Name</dt>
          <dd>{item.filename}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>
            {item.width && item.height ? `${item.width}×${item.height} · ` : ''}
            {fmtBytes(item.size_bytes)}
          </dd>
        </div>
        <div>
          <dt>Added</dt>
          <dd>{new Date(item.created_at).toLocaleDateString()}</dd>
        </div>
        <div>
          <dt>Tags</dt>
          <dd>{item.tags.length ? item.tags.map((t) => <TagChipInline key={t} tag={t} />) : 'none'}</dd>
        </div>
        {item.rating > 0 && (
          <div>
            <dt>Rating</dt>
            <dd>{'★'.repeat(item.rating)}</dd>
          </div>
        )}
        {item.source_url && (
          <div>
            <dt>Source</dt>
            <dd className="mono">{new URL(item.source_url).host}</dd>
          </div>
        )}
      </dl>
    </div>
  )
}

function TagChipInline({ tag }: { tag: string }) {
  return (
    <span className="mini-tag">
      <TagText tag={tag} hash={false} />
    </span>
  )
}

// ---------- stacks ----------

const STACK_KIND: Record<string, string> = { raw_jpeg: 'RAW + JPEG', burst: 'Burst', version: 'Versions' }

function StacksSection() {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['stackSuggestions'], queryFn: org.stackSuggestions, retry: false })
  const [dismissed, dismiss] = useDismissed('stacks')
  const caps = useCaps()
  const after = useAfterStackChange()
  const run = useBatchRun()
  const id = useId()
  const list = (data ?? []).filter((s) => !dismissed.has(s.item_ids.join(',')))
  return (
    <section aria-labelledby={`${id}-h`}>
      <div className="settings-section-head">
        <h2 id={`${id}-h`}>Stacks</h2>
      </div>
      <p className="settings-intro">
        A stack shows a RAW and its JPEG, a burst or several versions as one item in the grid, with a count on it; press
        the count (or S) to open it in place. To stack anything yourself, select the items in the grid and press Stack.
      </p>
      {isLoading && (
        <p className="hint" role="status">
          Looking…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load suggestions: {describeError(error)}</p>}
      {data && list.length === 0 && (
        <p className="empty-note" role="status">
          No stacks to suggest: no RAW+JPEG pairs, bursts or version chains outside a stack.
        </p>
      )}
      <ul className="suggest-list">
        {list.map((s) => (
          <StackSuggestionRow
            key={s.item_ids.join(',')}
            s={s}
            canEdit={caps.canEdit}
            onSkip={() => dismiss(s.item_ids.join(','))}
            onStack={() =>
              void run(
                { key: 'stack', fail: "Couldn't stack them", done: () => `Stacked ${s.item_ids.length} items.` },
                (batch) => org.createStack(s.item_ids, s.cover_item_id, batch),
                after,
              )
            }
          />
        ))}
      </ul>
    </section>
  )
}

function StackSuggestionRow({ s, canEdit, onSkip, onStack }: { s: StackSuggestion; canEdit: boolean; onSkip: () => void; onStack: () => void }) {
  return (
    <li className="suggest-card">
      <div className="suggest-head">
        <span className="pill">{STACK_KIND[s.kind] ?? s.kind}</span>
        <span>{s.reason}</span>
      </div>
      <div className="thumb-strip">
        {s.item_ids.slice(0, 8).map((i) => (
          <img key={i} src={thumbOf(i)} alt="" loading="lazy" className={i === s.cover_item_id ? 'cover' : ''} />
        ))}
        {s.item_ids.length > 8 && <span className="more">+{s.item_ids.length - 8}</span>}
      </div>
      {canEdit && (
        <div className="btn-row">
          <button className="primary" onClick={onStack}>
            Stack these {s.item_ids.length}
          </button>
          <button onClick={onSkip}>Skip</button>
        </div>
      )}
    </li>
  )
}

// ---------- suggested collections ----------

function CollectionsSection() {
  const [refresh, setRefresh] = useState(0)
  const { data, isLoading, isError, error, isFetching } = useQuery({
    queryKey: ['collections', refresh],
    queryFn: () => org.collections(refresh > 0),
    retry: false,
    staleTime: 5 * 60_000,
  })
  const [dismissed, dismiss] = useDismissed('collections')
  const caps = useCaps()
  const id = useId()
  const list = (data?.clusters ?? []).filter((c) => !dismissed.has(c.id))
  return (
    <section aria-labelledby={`${id}-h`}>
      <div className="settings-section-head">
        <h2 id={`${id}-h`}>You seem to collect…</h2>
        <button className="btn-icon" onClick={() => setRefresh((n) => n + 1)} disabled={isFetching}>
          <RefreshCw size={14} strokeWidth={1.75} aria-hidden="true" className={isFetching ? 'spin' : ''} />
          Look again
        </button>
      </div>
      <p className="settings-intro">
        Groups of look-alike items that aren’t in one folder yet. Keep a group as a folder, or as a tag with a smart
        folder that finds it. Kist looks again once a day{data?.computed_at ? ` (last ${timeAgo(data.computed_at)})` : ''}.
      </p>
      {(isLoading || (isFetching && refresh > 0)) && (
        <p className="hint" role="status">
          Looking for groups…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load suggestions: {describeError(error)}</p>}
      {data && list.length === 0 && (
        <p className="empty-note" role="status">
          No new groups. They show up once there are 8 or more look-alike items outside a folder.
        </p>
      )}
      <ul className="collection-list">
        {list.map((c) => (
          <CollectionCard key={c.id} c={c} canEdit={caps.canEdit} onDismiss={() => dismiss(c.id)} />
        ))}
      </ul>
    </section>
  )
}

function CollectionCard({ c, canEdit, onDismiss }: { c: Cluster; canEdit: boolean; onDismiss: () => void }) {
  const label = c.label_suggestion ?? ''
  // brand:rolex -> "Rolex"
  const base = splitTag(label).value
  const [name, setName] = useState(base ? base.charAt(0).toUpperCase() + base.slice(1) : '')
  const [kind, setKind] = useState<'folder' | 'smart_folder'>('folder')
  const [all, setAll] = useState(false)
  const [done, setDone] = useState(false)
  const qc = useQueryClient()
  const refresh = useRefresh()
  const run = useBatchRun()
  const id = useId()
  const ids = all ? c.item_ids : c.sample_item_ids.slice(0, 8)
  const after = () => {
    refresh.folders()
    refresh.smartFolders()
    refresh.itemsAdded()
    void qc.invalidateQueries({ queryKey: ['collections'] })
  }
  const accept = async () => {
    const ok = await run(
      {
        key: 'collection',
        fail: "Couldn't make it",
        done: () => (kind === 'folder' ? `Made the folder “${name}” with ${plural(c.size, 'item')}.` : `Tagged ${plural(c.size, 'item')} and saved the smart folder “${name}”.`),
      },
      (batch) => org.acceptCollection(c.id, name.trim(), kind, batch),
      after,
    )
    if (ok) setDone(true)
  }
  if (done) return null
  return (
    <li className="collection-card" aria-labelledby={`${id}-t`}>
      <h3 id={`${id}-t`}>
        {label ? (
          <>
            <TagText tag={label} hash={false} />
          </>
        ) : (
          'A group of look-alikes'
        )}
        <span className="hint-inline"> · {plural(c.size, 'item')}</span>
      </h3>
      <div className="thumb-strip">
        {ids.map((i) => (
          <img key={i} src={thumbOf(i)} alt="" loading="lazy" />
        ))}
      </div>
      {c.size > 8 && (
        <button className="link-btn" onClick={() => setAll((a) => !a)} aria-expanded={all}>
          {all ? 'Show fewer' : `Show all ${c.size}`}
        </button>
      )}
      <p className="hint">
        {c.label_source === 'tags' && c.common_tags?.length
          ? `Most share the tag “${c.common_tags[0].tag}” (${c.common_tags[0].count} of ${c.size}).`
          : c.label_source === 'zero_shot'
            ? 'The name is a guess from what the pictures show.'
            : ''}
        {c.top_folder?.name ? ` ${c.top_folder.count} are already in “${c.top_folder.name}”.` : ''}
      </p>
      {canEdit && (
        <div className="collection-accept">
          <label htmlFor={`${id}-n`}>Name</label>
          <input id={`${id}-n`} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
          <label htmlFor={`${id}-k`}>Keep as</label>
          <select id={`${id}-k`} value={kind} onChange={(e) => setKind(e.target.value as 'folder' | 'smart_folder')}>
            <option value="folder">a folder</option>
            <option value="smart_folder">a tag + smart folder</option>
          </select>
          <div className="btn-row">
            <button className="primary" disabled={!name.trim()} onClick={() => void accept()}>
              <Check size={14} strokeWidth={2} aria-hidden="true" /> Keep
            </button>
            <button onClick={onDismiss}>Not now</button>
          </div>
        </div>
      )}
    </li>
  )
}

// ---------- people ----------

function PeopleSection() {
  const flags = useAiFlags()
  const caps = useCaps()
  const { setQuery } = useAppState()
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['people'], queryFn: org.people, retry: false })
  const qc = useQueryClient()
  const dialog = useDialog()
  const { notify } = useNotices()
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const id = useId()
  const people = data ?? []
  const pickedPeople = useMemo(() => people.filter((p) => picked.has(p.id)), [people, picked])

  const reload = () => {
    void qc.invalidateQueries({ queryKey: ['people'] })
    void qc.invalidateQueries({ queryKey: ['faces'] })
  }
  const name = async (p: Person) => {
    const v = await dialog.prompt({ title: p.name ? `Rename ${p.name}` : 'Who is this?', label: 'Name', initial: p.name ?? '', confirmLabel: 'Save' })
    if (!v) return
    try {
      await org.patchPerson(p.id, { name: v })
      reload()
    } catch (e) {
      notify({ tone: 'error', message: `Couldn't save the name: ${describeError(e)}` })
    }
  }
  const merge = async (into: Person) => {
    const from = pickedPeople.filter((p) => p.id !== into.id)
    const ok = await dialog.confirm({
      title: `Merge ${from.length === 1 ? '1 person' : `${from.length} people`} into ${into.name ?? 'this person'}?`,
      message: 'Their faces all become one person. To split them again, move single faces in an item’s info panel.',
      confirmLabel: 'Merge',
    })
    if (!ok) return
    try {
      await org.mergePeople(from.map((p) => p.id), into.id)
      setPicked(new Set())
      reload()
      notify({ message: `Merged into ${into.name ?? 'one person'}.` })
    } catch (e) {
      notify({ tone: 'error', message: `Couldn't merge: ${describeError(e)}` })
    }
  }

  return (
    <section aria-labelledby={`${id}-h`}>
      <div className="settings-section-head">
        <h2 id={`${id}-h`}>People</h2>
      </div>
      {flags.ready && !flags.faces && people.length === 0 ? (
        <div className="health-card">
          <h3>Faces are off</h3>
          <p>
            Kist can find faces in your photos and group them into people for you to name, entirely on your own
            hardware. It’s off because face data is personal. To turn it on, set <code>KIST_FACES=1</code> for the
            app, worker and ml service, then run <code>kist ai-backfill --kind faces</code>.
          </p>
        </div>
      ) : (
        <>
          <p className="settings-intro">
            Faces found in your photos, grouped by who they seem to be. Name someone to find them anywhere; select two
            or more to merge them.
          </p>
          {isLoading && (
            <p className="hint" role="status">
              Loading…
            </p>
          )}
          {isError && <p role="alert">Couldn’t load people: {describeError(error)}</p>}
          {data && people.length === 0 && (
            <p className="empty-note" role="status">
              No faces found yet. New photos are looked at as they arrive.
            </p>
          )}
          {pickedPeople.length >= 2 && caps.canEdit && (
            <div className="bulk-line" role="group" aria-label="Merge the selected people">
              <span>{pickedPeople.length} selected. Merge into:</span>
              {pickedPeople.map((p) => (
                <button key={p.id} className="btn-icon" onClick={() => void merge(p)}>
                  {p.cover && <img className="chip-thumb round" src={`${p.cover.thumb_url}?size=64`} alt="" />}
                  {p.name ?? `Unnamed (${p.face_count} faces)`}
                </button>
              ))}
              <button className="icon-btn" aria-label="Clear selection" onClick={() => setPicked(new Set())}>
                <X size={14} strokeWidth={1.75} aria-hidden="true" />
              </button>
            </div>
          )}
          <ul className="people-grid">
            {people.map((p) => (
              <li key={p.id} className={`person-card${picked.has(p.id) ? ' picked' : ''}`}>
                <button
                  type="button"
                  className="person-face"
                  onClick={() => setQuery({ ...emptyQuery, person: p.id })}
                  title={`Show the ${plural(p.item_count, 'item')} with ${p.name ?? 'this person'}`}
                >
                  {p.cover ? <img src={`${p.cover.thumb_url}?size=160`} alt="" /> : <Users size={32} aria-hidden="true" />}
                  <span className="person-name">{p.name ?? 'Unnamed'}</span>
                  <span className="hint-inline">{plural(p.item_count, 'item')}</span>
                </button>
                {caps.canEdit && (
                  <div className="person-actions">
                    <button type="button" className="link-btn" onClick={() => void name(p)}>
                      {p.name ? 'Rename' : 'Name'}
                      <span className="sr-only"> {p.name ?? 'this person'}</span>
                    </button>
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={picked.has(p.id)}
                        onChange={(e) =>
                          setPicked((s) => {
                            const n = new Set(s)
                            if (e.target.checked) n.add(p.id)
                            else n.delete(p.id)
                            return n
                          })
                        }
                      />
                      Select<span className="sr-only"> {p.name ?? `unnamed person with ${p.face_count} faces`}</span>
                    </label>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
