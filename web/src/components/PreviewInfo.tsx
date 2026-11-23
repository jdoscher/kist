// The full-screen preview's info strip (D / Info): where the item came from
// and what you've said about it. Editors set the rating and add or remove
// tags right here (same writes as the info panel: one PATCH per change,
// tags sent as {add}/{remove} so nothing else is overwritten).

import { ExternalLink, Star, X } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { api, type Item } from '../api'
import { useTags } from '../data'
import { completeFragment, parseTagInput, tagSuggestions } from '../phoneEdit'
import { infoRows } from '../previewInfo'
import { useItemWrite } from '../useWrite'
import { TagText } from './ItemAI'

export function PreviewInfo({ item, canEdit }: { item: Item; canEdit: boolean }) {
  const rows = infoRows(item, { editable: canEdit })
  return (
    <section className="pinfo" aria-label="Item info">
      <dl>
        {rows.map((r) => (
          <div key={r.label} className={`pinfo-row${canEdit && (r.label === 'Tags' || r.label === 'Rating') ? ' pinfo-edit' : ''}`}>
            <dt>{r.label}</dt>
            {canEdit && r.label === 'Rating' ? (
              <dd>
                <PinfoStars item={item} />
              </dd>
            ) : canEdit && r.label === 'Tags' ? (
              <dd>
                <PinfoTags item={item} />
              </dd>
            ) : (
              <dd title={r.text}>
                {r.href ? (
                  <a href={r.href} target="_blank" rel="noreferrer noopener">
                    {r.text} <ExternalLink size={14} aria-hidden="true" />
                    <span className="sr-only"> (new tab)</span>
                  </a>
                ) : (
                  r.text
                )}
              </dd>
            )}
          </div>
        ))}
      </dl>
    </section>
  )
}

/** Click a star to rate, the same star again to clear. */
function PinfoStars({ item }: { item: Item }) {
  const { write } = useItemWrite(item.id)
  const value = item.rating
  return (
    <div className="pinfo-stars" role="group" aria-label={`Rating: ${value ? `${value} of 5` : 'none'}`}>
      {[1, 2, 3, 4, 5].map((s) => (
        <button
          key={s}
          type="button"
          className={s <= value ? 'on' : ''}
          aria-label={`${s} star${s > 1 ? 's' : ''}`}
          aria-pressed={s <= value}
          title={s === value ? 'Click again to clear the rating' : `${s} star${s > 1 ? 's' : ''}`}
          onClick={() => {
            const rating = s === value ? 0 : s
            void write('rating', () => api.patchItem(item.id, { rating }))
          }}
        >
          <Star size={16} fill={s <= value ? 'currentColor' : 'none'} aria-hidden="true" />
        </button>
      ))}
      {!value && <span className="pinfo-dim">not rated</span>}
    </div>
  )
}

/** The tags as chips with ✕, then a field: Enter adds (comma-separated),
 * matching tags in the library are offered as you type. */
function PinfoTags({ item }: { item: Item }) {
  const { write } = useItemWrite(item.id)
  const { data: allTags } = useTags()
  const [text, setText] = useState('')
  const uid = useId()
  const suggestions = useMemo(() => tagSuggestions(allTags ?? [], text, item.tags), [allTags, text, item.tags])

  const add = async (value = text) => {
    const tags = parseTagInput(value).filter((t) => !item.tags.includes(t))
    setText('')
    if (!tags.length) return
    const ok = await write('tags', () => api.patchTags(item.id, { add: tags }))
    if (!ok) setText(value) // what was typed comes back to try again
  }
  const remove = (tag: string) => void write('tags', () => api.patchTags(item.id, { remove: [tag] }))

  return (
    <div className="pinfo-tags">
      {item.tags.length > 0 && (
        <ul className="pinfo-chips" aria-label="Tags on this item">
          {item.tags.map((t) => (
            <li key={t} className="pinfo-chip">
              <TagText tag={t} />
              <button type="button" aria-label={`Remove tag ${t}`} title={`Remove tag ${t}`} onClick={() => remove(t)}>
                <X size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="pinfo-tag-add"
        onSubmit={(e) => {
          e.preventDefault()
          void add()
        }}
      >
        <label htmlFor={`${uid}-tag`} className="sr-only">
          Add tags
        </label>
        <input
          id={`${uid}-tag`}
          value={text}
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          placeholder="Add tag"
          aria-describedby={`${uid}-hint`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // Escape leaves the field (the preview closes on the next one)
            if (e.key === 'Escape' && text) {
              e.preventDefault()
              setText('')
            } else if (e.key === 'Escape') (e.target as HTMLInputElement).blur()
          }}
        />
        <span id={`${uid}-hint`} className="sr-only">
          Separate several tags with commas; Enter adds them. Matching tags are listed after the field.
        </span>
      </form>
      {suggestions.length > 0 && (
        <ul className="pinfo-suggest" aria-label="Matching tags">
          {suggestions.map((s) => (
            <li key={s}>
              <button type="button" aria-label={`Add tag ${s}`} onClick={() => void add(completeFragment(text, s))}>
                <TagText tag={s} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
