// Settings → Vocabulary (aliases, implied tags, namespaces) and Settings →
// Tag cleanup (suggestions with dry-run counts, merge and rename, Undo in
// the notice). Loaded when first opened.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Check, Trash2 } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { api, emptyQuery, newBatchId } from '../api'
import { useAfterUndo, useSmartFolders, useTags } from '../data'
import { plural } from '../format'
import { timeAgo, undoReport } from '../history'
import { org, type CleanupKind, type CleanupSuggestion, type MergeResult } from '../organiseApi'
import { useAppState } from '../state'
import { namespaceWildcard } from '../tagNs'
import { describeError } from '../writes'
import { useDialog } from './Dialog'
import { TagText } from './ItemAI'
import { useNotices } from './Notices'

function useTagRefresh() {
  const qc = useQueryClient()
  return () => {
    for (const k of ['vocabulary', 'tagCleanup', 'tags', 'smartFolders', 'tagSuggestions', 'item', 'stats'])
      void qc.invalidateQueries({ queryKey: [k] })
    void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
  }
}

/** Undo for a tag change: revert its batch on the server, then refresh.
 * The batch holds the rule changes too (an alias a merge left, a new
 * implied tag), so this is the same undo as Recent changes and MCP. */
function useUndoNotice() {
  const { notify } = useNotices()
  const afterUndo = useAfterUndo()
  const refresh = useTagRefresh()
  return (key: string, message: string, batch: string) =>
    notify({
      key,
      message,
      action: {
        label: 'Undo',
        run: () =>
          void api.undoBatch(batch).then(
            (r) => {
              afterUndo()
              refresh()
              const rep = undoReport(r, 'Undid')
              notify({ key, tone: rep.tone, message: rep.tone === 'info' ? 'Undone.' : rep.message })
            },
            (e) => notify({ key, tone: 'error', message: `Couldn't undo: ${describeError(e)}` }),
          ),
      },
    })
}

// ---------- vocabulary ----------

export function VocabularySection() {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['vocabulary'], queryFn: org.vocabulary })
  const { data: tags } = useTags()
  const { setQuery } = useAppState()
  const { notify } = useNotices()
  const undoNotice = useUndoNotice()
  const refresh = useTagRefresh()
  const id = useId()
  const [alias, setAlias] = useState('')
  const [aliasTo, setAliasTo] = useState('')
  const [impTag, setImpTag] = useState('')
  const [impTo, setImpTo] = useState('')
  const [aliasErr, setAliasErr] = useState('')
  const [impErr, setImpErr] = useState('')
  const [busy, setBusy] = useState(false)

  const addAlias = async (e: React.FormEvent) => {
    e.preventDefault()
    const a = alias.trim()
    const t = aliasTo.trim()
    if (!a || !t) return
    setBusy(true)
    setAliasErr('')
    const batch = newBatchId()
    try {
      const r = await org.addAlias(a, t, batch)
      setAlias('')
      setAliasTo('')
      refresh()
      undoNotice(
        'vocab',
        `“${r.alias}” now means “${r.tag}”${r.items ? `: ${plural(r.items, 'item')} retagged` : ''}.`,
        batch,
      )
    } catch (err) {
      setAliasErr(describeError(err))
    } finally {
      setBusy(false)
    }
  }
  const addImplies = async (e: React.FormEvent) => {
    e.preventDefault()
    const a = impTag.trim()
    const b = impTo.trim()
    if (!a || !b) return
    setBusy(true)
    setImpErr('')
    try {
      const batch = newBatchId()
      await org.addImplies(a, b, batch)
      setImpTag('')
      setImpTo('')
      refresh()
      undoNotice('vocab', `Searching #${b} now also finds items tagged ${a}.`, batch)
    } catch (err) {
      setImpErr(describeError(err))
    } finally {
      setBusy(false)
    }
  }
  const remove = async (what: string, fn: () => Promise<unknown>) => {
    try {
      await fn()
      refresh()
      notify({ key: 'vocab', message: what })
    } catch (err) {
      notify({ key: 'vocab', tone: 'error', message: `Couldn't remove it: ${describeError(err)}` })
    }
  }

  return (
    <section aria-labelledby={`${id}-h`}>
      <div className="settings-section-head">
        <h2 id={`${id}-h`}>Vocabulary</h2>
      </div>
      <p className="settings-intro">
        The words your library uses. An alias maps a variant to the tag you prefer, everywhere: on the items that carry
        it now and whenever you, an import or an agent uses it later. An implied tag is found by search but never
        written to items: if Boots implies Shoes, searching #Shoes finds your boots.
      </p>
      <datalist id={`${id}-tags`}>
        {(tags ?? []).map((t) => (
          <option key={t.id} value={t.name} />
        ))}
      </datalist>
      {isLoading && (
        <p className="hint" role="status">
          Loading…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load the vocabulary: {describeError(error)}</p>}
      {data && (
        <>
          <div className="health-card">
            <h3>Aliases</h3>
            {data.aliases.length === 0 ? (
              <p className="hint">No aliases yet.</p>
            ) : (
              <ul className="rule-list">
                {data.aliases.map((a) => (
                  <li key={a.alias}>
                    <span className="rule">
                      <TagText tag={a.alias} hash={false} />
                      <ArrowRight size={14} strokeWidth={1.75} aria-label="means" />
                      <strong>
                        <TagText tag={a.tag} hash={false} />
                      </strong>
                    </span>
                    <button
                      className="icon-btn"
                      aria-label={`Remove alias ${a.alias}`}
                      title="Remove the alias (tags already rewritten stay)"
                      onClick={() => void remove(`“${a.alias}” is no longer an alias.`, () => org.removeAlias(a.alias))}
                    >
                      <Trash2 size={14} strokeWidth={1.75} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <form className="rule-form" onSubmit={(e) => void addAlias(e)}>
              <label>
                <span>Variant</span>
                <input value={alias} onChange={(e) => setAlias(e.target.value)} list={`${id}-tags`} placeholder="Fashion" />
              </label>
              <label>
                <span>means</span>
                <input value={aliasTo} onChange={(e) => setAliasTo(e.target.value)} list={`${id}-tags`} placeholder="Clothing" />
              </label>
              <button type="submit" disabled={busy || !alias.trim() || !aliasTo.trim()}>
                Add alias
              </button>
            </form>
            {aliasErr && (
              <p className="error" role="alert">
                {aliasErr}
              </p>
            )}
          </div>

          <div className="health-card">
            <h3>Implied tags</h3>
            {data.implies.length === 0 ? (
              <p className="hint">No implied tags yet.</p>
            ) : (
              <ul className="rule-list">
                {data.implies.map((r) => (
                  <li key={`${r.tag}>${r.implied}`}>
                    <span className="rule">
                      <TagText tag={r.tag} hash={false} />
                      <ArrowRight size={14} strokeWidth={1.75} aria-label="implies" />
                      <strong>
                        <TagText tag={r.implied} hash={false} />
                      </strong>
                    </span>
                    <button
                      className="icon-btn"
                      aria-label={`Remove: ${r.tag} implies ${r.implied}`}
                      title="Remove the rule (nothing on the items changes)"
                      onClick={() => void remove(`${r.tag} no longer implies ${r.implied}.`, () => org.removeImplies(r.tag, r.implied))}
                    >
                      <Trash2 size={14} strokeWidth={1.75} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <form className="rule-form" onSubmit={(e) => void addImplies(e)}>
              <label>
                <span>Tag</span>
                <input value={impTag} onChange={(e) => setImpTag(e.target.value)} list={`${id}-tags`} placeholder="Boots" />
              </label>
              <label>
                <span>implies</span>
                <input value={impTo} onChange={(e) => setImpTo(e.target.value)} list={`${id}-tags`} placeholder="Shoes" />
              </label>
              <button type="submit" disabled={busy || !impTag.trim() || !impTo.trim()}>
                Add rule
              </button>
            </form>
            {impErr && (
              <p className="error" role="alert">
                {impErr}
              </p>
            )}
          </div>

          <div className="health-card">
            <h3>Namespaces</h3>
            <p className="hint">
              Tags written <code>namespace:value</code> (brand:Rolex, style:minimal) are grouped. Search a whole namespace
              with <code>#brand:*</code>.
            </p>
            {data.namespaces.length === 0 ? (
              <p className="hint">No namespaced tags yet.</p>
            ) : (
              <ul className="ns-list">
                {data.namespaces.map((n) => (
                  <li key={n.namespace}>
                    <button
                      className="chip"
                      onClick={() => setQuery({ ...emptyQuery, tags: [namespaceWildcard(n.namespace)] })}
                      title={`Show every item tagged ${n.namespace}:…`}
                    >
                      <span className="ns-tag">
                        <span className="ns-part">{n.namespace}</span>
                        <span className="ns-value">{plural(n.tags, 'tag')}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  )
}

// ---------- tag cleanup ----------

const KIND_HEAD: Record<CleanupKind, { title: string; hint: string }> = {
  clash: { title: 'Same tag, written differently', hint: 'Case, spaces or hyphens differ (dark mode / dark-mode).' },
  near: { title: 'Near spellings and plurals', hint: 'One or two letters apart, or the same word stem (Restaurant / Restaurants).' },
  smart_folder: { title: 'Smart folders with missing tags or folders', hint: 'They name a tag or folder that no longer exists, so they find less than they used to.' },
  one_off: { title: 'Used once', hint: 'Tags on a single item: typos, tests, or the start of something.' },
  unused: { title: 'Unused', hint: 'No item uses these now, or none was tagged with them in the last year.' },
}
const KIND_ORDER: CleanupKind[] = ['clash', 'near', 'smart_folder', 'one_off', 'unused']

export function CleanupSection() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({ queryKey: ['tagCleanup'], queryFn: org.cleanup })
  const id = useId()
  const by = useMemo(() => {
    const m = new Map<CleanupKind, CleanupSuggestion[]>()
    for (const s of data ?? []) m.set(s.kind, [...(m.get(s.kind) ?? []), s])
    return m
  }, [data])
  return (
    <section aria-labelledby={`${id}-h`}>
      <div className="settings-section-head">
        <h2 id={`${id}-h`}>Tag cleanup</h2>
        <button onClick={() => void refetch()} disabled={isFetching}>
          Check again
        </button>
      </div>
      <p className="settings-intro">
        Suggestions for a tidier set of tags. Every merge or rename says first how many items it changes, runs as one
        batch, and can be undone from the notice or Recent changes.
      </p>
      {isLoading && (
        <p className="hint" role="status">
          Looking…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load suggestions: {describeError(error)}</p>}
      {data && data.length === 0 && (
        <p className="empty-note" role="status">
          Nothing to tidy. <Check size={14} strokeWidth={2} aria-hidden="true" />
        </p>
      )}
      {KIND_ORDER.filter((k) => by.has(k)).map((k) => (
        <div key={k} className="health-card cleanup-group">
          <h3>
            {KIND_HEAD[k].title} <span className="hint-inline">({by.get(k)!.length})</span>
          </h3>
          <p className="hint">{KIND_HEAD[k].hint}</p>
          {k === 'clash' || k === 'near' ? (
            <ul className="cleanup-list">
              {by.get(k)!.map((s) => (
                <MergeRow key={s.tags.join('|')} s={s} />
              ))}
            </ul>
          ) : k === 'smart_folder' ? (
            <SmartFolderRows list={by.get(k)!} />
          ) : (
            <SingleTagRows list={by.get(k)!} kind={k} />
          )}
        </div>
      ))}
    </section>
  )
}

/** Dry run, then ask: "Merge … into …? N items change." */
function useMerge() {
  const dialog = useDialog()
  const { notify } = useNotices()
  const undoNotice = useUndoNotice()
  const refresh = useTagRefresh()
  const describe = (r: MergeResult) =>
    `${plural(r.items, 'item')} ${r.items === 1 ? 'changes' : 'change'}${r.smart_folders?.length ? `, and ${plural(r.smart_folders.length, 'smart folder')} ${r.smart_folders.length === 1 ? 'is' : 'are'} updated` : ''}.`
  return {
    merge: async (from: string[], into: string, keepAlias: boolean) => {
      try {
        const dry = await org.mergeTags(from, into, { dryRun: true, keepAlias })
        const ok = await dialog.confirm({
          title: `Merge ${from.map((t) => `“${t}”`).join(', ')} into “${dry.into}”?`,
          message: `${describe(dry)}${keepAlias ? ' The old names stay as aliases, so they’re rewritten if used again.' : ''}`,
          confirmLabel: 'Merge',
        })
        if (!ok) return
        const batch = newBatchId()
        const r = await org.mergeTags(from, into, { keepAlias, batch })
        refresh()
        // the server's undo of the batch puts the tags back and drops the
        // aliases the merge left (they're in the same batch)
        undoNotice('cleanup', `Merged into “${r.into}”: ${plural(r.items, 'item')} retagged.`, batch)
      } catch (e) {
        notify({ key: 'cleanup', tone: 'error', message: `Couldn't merge: ${describeError(e)}` })
      }
    },
    rename: async (from: string) => {
      const to = await dialog.prompt({ title: `Rename “${from}”`, label: 'New name', initial: from, confirmLabel: 'Next' })
      if (!to || to === from) return
      try {
        const dry = await org.renameTag(from, to, { dryRun: true })
        const ok = await dialog.confirm({
          title: `Rename “${from}” to “${to}”?`,
          message: describe(dry),
          confirmLabel: 'Rename',
        })
        if (!ok) return
        const batch = newBatchId()
        const r = await org.renameTag(from, to, { batch })
        refresh()
        undoNotice('cleanup', `Renamed “${from}” to “${r.into}”.`, batch)
      } catch (e) {
        notify({ key: 'cleanup', tone: 'error', message: `Couldn't rename: ${describeError(e)}` })
      }
    },
  }
}

function MergeRow({ s }: { s: CleanupSuggestion }) {
  const [into, setInto] = useState(s.proposal.into ?? s.tags[0])
  const [keepAlias, setKeepAlias] = useState(true)
  const { merge } = useMerge()
  const id = useId()
  const from = s.tags.filter((t) => t !== into)
  return (
    <li className="cleanup-row">
      <div className="cleanup-tags">
        {s.tags.map((t) => (
          <span key={t} className={`mini-tag${t === into ? ' target' : ''}`}>
            <TagText tag={t} hash={false} /> <span className="count">{s.counts[t] ?? 0}</span>
          </span>
        ))}
      </div>
      <div className="cleanup-actions">
        <label htmlFor={`${id}-into`}>Keep</label>
        <select id={`${id}-into`} value={into} onChange={(e) => setInto(e.target.value)}>
          {s.tags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <label className="check-row">
          <input type="checkbox" checked={keepAlias} onChange={(e) => setKeepAlias(e.target.checked)} />
          keep the others as aliases
        </label>
        <button className="primary" onClick={() => void merge(from, into, keepAlias)}>
          Merge…
        </button>
      </div>
    </li>
  )
}

function SingleTagRows({ list, kind }: { list: CleanupSuggestion[]; kind: CleanupKind }) {
  const { setQuery } = useAppState()
  const { rename } = useMerge()
  const { notify } = useNotices()
  const refresh = useTagRefresh()
  const dialog = useDialog()
  const empty = list.filter((s) => (s.counts[s.tags[0]] ?? 0) === 0).length
  const deleteUnused = async () => {
    const ok = await dialog.confirm({ title: `Delete ${plural(empty, 'tag')} that no item uses?`, confirmLabel: 'Delete', danger: true })
    if (!ok) return
    try {
      await org.deleteUnusedTags()
      refresh()
      notify({ key: 'cleanup', message: 'Deleted the unused tags.' })
    } catch (e) {
      notify({ key: 'cleanup', tone: 'error', message: `Couldn't delete them: ${describeError(e)}` })
    }
  }
  return (
    <>
      {kind === 'unused' && empty > 0 && (
        <button className="danger-outline" onClick={() => void deleteUnused()}>
          Delete the {plural(empty, 'tag')} no item uses
        </button>
      )}
      <ul className="cleanup-list compact">
        {list.map((s) => {
          const t = s.tags[0]
          const n = s.counts[t] ?? 0
          return (
            <li key={t} className="cleanup-row">
              <span className="mini-tag">
                <TagText tag={t} hash={false} /> <span className="count">{n}</span>
              </span>
              {s.last_used && n > 0 && <span className="hint-inline">last tagged {timeAgo(s.last_used)}</span>}
              <span className="cleanup-actions">
                {n > 0 && (
                  <button className="link-btn" onClick={() => setQuery({ ...emptyQuery, tags: [t] })}>
                    Show<span className="sr-only"> items tagged {t}</span>
                  </button>
                )}
                {n > 0 && (
                  <button className="link-btn" onClick={() => void rename(t)}>
                    Rename<span className="sr-only"> {t}</span>
                  </button>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </>
  )
}

function SmartFolderRows({ list }: { list: CleanupSuggestion[] }) {
  const { data: smart } = useSmartFolders()
  const { applySmartFolder } = useAppState()
  return (
    <ul className="cleanup-list">
      {list.map((s) => {
        const p = s.proposal
        const sf = (smart ?? []).find((x) => x.id === p.smart_folder_id)
        return (
          <li key={p.smart_folder_id} className="cleanup-row">
            <span>
              <strong>{p.smart_folder}</strong>{' '}
              {p.missing_tags?.length ? `names ${p.missing_tags.map((t) => `#${t}`).join(', ')}` : ''}
              {p.missing_tags?.length && p.missing_folder ? ' and ' : ''}
              {p.missing_folder ? 'a deleted folder' : ''}
            </span>
            {sf && (
              <button className="link-btn" onClick={() => applySmartFolder(sf.id, sf.query)}>
                Open it
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}
