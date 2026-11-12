// /add: the bookmarklet's confirm card (kist-ext docs/add-route.md). Any
// site can open /add?url=…, so nothing is saved without a click on Save,
// parameters are text (never HTML), and only http(s) URLs become src/href.

import { Check, ImageOff } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ApiError, json, request, type Item } from '../../api'
import { useFolders, useTags } from '../../data'
import { addErrorText, addPayload, hostOf, parseAddParams } from '../../m45/add'
import { Mark, Wordmark } from '../Brand'

const RECENT_KEY = 'kist_add_recent_folders'

function recentFolders(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, 5) : []
  } catch {
    return []
  }
}

function rememberFolder(id: string) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...recentFolders().filter((x) => x !== id)].slice(0, 5)))
  } catch {
    /* private mode */
  }
}

type Result = { kind: 'saved' | 'exists'; item: Item } | null

export default function AddPage({ search }: { search: string }) {
  const p = useMemo(() => parseAddParams(search), [search])
  const [chosen, setChosen] = useState(p.url)
  const [broken, setBroken] = useState<Set<string>>(new Set())
  const [tags, setTags] = useState('')
  const [folder, setFolder] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<Result>(null)
  const folders = useFolders()
  const tagList = useTags()
  const id = useId()
  const saveRef = useRef<HTMLButtonElement>(null)
  const recents = recentFolders()
  const sortedFolders = useMemo(() => {
    const list = [...(folders.data ?? [])]
    const rank = (fid: string) => {
      const i = recents.indexOf(fid)
      return i < 0 ? 99 : i
    }
    return list.sort((a, b) => rank(a.id) - rank(b.id) || a.name.localeCompare(b.name))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folders.data])

  useEffect(() => {
    document.title = 'Save to Kist'
    saveRef.current?.focus()
  }, [])

  const cancel = () => {
    window.close()
    // not opened by a script (or the browser refused): go back, or home
    setTimeout(() => {
      if (p.returnToPage && p.page) window.location.href = p.page
      else if (window.history.length > 1) window.history.back()
      else window.location.href = '/'
    }, 100)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!chosen || busy) return
    setBusy(true)
    setError('')
    try {
      const body = addPayload(p, chosen, {
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        folderIds: folder ? [folder] : [],
        notes,
      })
      const r = await request<{ item: Item; created: boolean }>('/api/items', json(body), 150_000)
      if (folder) rememberFolder(folder)
      setResult({ kind: r.created ? 'saved' : 'exists', item: r.item })
      if (r.created && !p.returnToPage) setTimeout(() => window.close(), 1500)
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      if (status === 401) return // the sign-in screen takes over, keeping this page
      setError(addErrorText(status, err instanceof Error ? err.message : ''))
    } finally {
      setBusy(false)
    }
  }

  const host = hostOf(p.page)
  const images = [p.url, ...p.alts].filter((u): u is string => !!u)

  return (
    <main className="add-page">
      <div className="add-card">
        <header className="add-head">
          <Mark className="add-mark" />
          <Wordmark className="add-wordmark" />
          <h1 className="sr-only">Save to Kist</h1>
        </header>
        {result ? (
          <div className="add-result" role="status">
            <p className="add-done">
              <Check size={16} aria-hidden="true" /> {result.kind === 'saved' ? 'Saved to Kist' : 'Already in Kist'}
            </p>
            <p className="add-fname">{result.item.filename}</p>
            <div className="dialog-actions">
              <a href={`/all?item=${encodeURIComponent(result.item.id)}`}>{result.kind === 'saved' ? 'Open it in Kist' : 'See it in Kist'}</a>
              {p.returnToPage && p.page ? (
                <button type="button" className="btn-primary" onClick={() => (window.location.href = p.page as string)} autoFocus>
                  Back to the page
                </button>
              ) : (
                <button type="button" className="btn-primary" onClick={() => window.close()} autoFocus>
                  Close
                </button>
              )}
            </div>
          </div>
        ) : !p.url ? (
          <div className="add-result">
            <p>There’s no image here Kist can save: the link didn’t include an http or https image address.</p>
            <div className="dialog-actions">
              <button type="button" onClick={cancel} autoFocus>
                Close
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={save} onKeyDown={(e) => e.key === 'Escape' && cancel()} className="add-form">
            <div className="add-preview">
              {chosen && !broken.has(chosen) ? (
                <img
                  src={chosen}
                  alt={p.title ? `Image from ${p.title}` : 'The image to save'}
                  referrerPolicy="no-referrer"
                  onError={() => setBroken((b) => new Set(b).add(chosen))}
                />
              ) : (
                <div className="add-noimg">
                  <ImageOff size={28} aria-hidden="true" />
                  <p>The preview didn’t load; Kist will try to download it.</p>
                  <p className="mono add-url">{chosen}</p>
                </div>
              )}
            </div>
            {images.length > 1 && (
              <fieldset className="add-alts">
                <legend className="sr-only">Pick the image</legend>
                {images.map((u, i) => (
                  <label key={u} className={`add-alt${chosen === u ? ' on' : ''}`} title={u}>
                    <input type="radio" name={`${id}-img`} className="sr-only" checked={chosen === u} onChange={() => setChosen(u)} />
                    {broken.has(u) ? <span className="add-alt-x">{i + 1}</span> : <img src={u} alt={`Image ${i + 1}`} referrerPolicy="no-referrer" onError={() => setBroken((b) => new Set(b).add(u))} />}
                  </label>
                ))}
              </fieldset>
            )}
            {(p.title || host) && (
              <p className="add-source">
                {p.title && <span className="add-title">{p.title}</span>}
                {host && <span className="hint"> {host}</span>}
              </p>
            )}
            <label htmlFor={`${id}-tags`}>Tags</label>
            <input id={`${id}-tags`} list={`${id}-taglist`} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="comma, separated" autoComplete="off" />
            <datalist id={`${id}-taglist`}>
              {(tagList.data ?? []).slice(0, 200).map((t) => (
                <option key={t.id} value={t.name} />
              ))}
            </datalist>
            <label htmlFor={`${id}-folder`}>Folder</label>
            <select id={`${id}-folder`} value={folder} onChange={(e) => setFolder(e.target.value)}>
              <option value="">No folder (Unfiled)</option>
              {sortedFolders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
            <label htmlFor={`${id}-notes`}>Note</label>
            <textarea id={`${id}-notes`} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="error" role="alert">
              {error}
            </div>
            <div className="dialog-actions">
              <button type="button" onClick={cancel}>
                Cancel
              </button>
              <button ref={saveRef} type="submit" className="btn-primary" disabled={busy}>
                {busy ? 'Saving…' : 'Save'}
              </button>
            </div>
          </form>
        )}
      </div>
    </main>
  )
}
