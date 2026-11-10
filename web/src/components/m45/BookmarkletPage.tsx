// /bookmarklet: the "Save to Kist" bookmarklet generator (kist-ext
// docs/bookmarklet.html, served by Kist). Needs no token: the bookmarklet
// never holds one. The Kist address is prefilled from this page's origin.

import { useEffect, useId, useRef, useState } from 'react'
import { bookmarkletFor, normalizeKistBase } from '../../m45/add'
import { Mark, Wordmark } from '../Brand'

const KEY = 'kist-bookmarklet-url'

export default function BookmarkletPage() {
  const [kist, setKist] = useState(() => {
    try {
      return localStorage.getItem(KEY) || window.location.origin
    } catch {
      return window.location.origin
    }
  })
  const link = useRef<HTMLAnchorElement>(null)
  const id = useId()
  const code = bookmarkletFor(kist)

  useEffect(() => {
    document.title = 'Save to Kist bookmarklet'
  }, [])

  // React refuses javascript: URLs in href, so the link is set directly
  useEffect(() => {
    const a = link.current
    if (!a) return
    if (code) {
      a.setAttribute('href', code)
      a.removeAttribute('aria-disabled')
    } else {
      a.setAttribute('href', '#')
      a.setAttribute('aria-disabled', 'true')
    }
    const base = normalizeKistBase(kist)
    try {
      if (base) localStorage.setItem(KEY, base)
    } catch {
      /* private mode */
    }
  }, [code, kist])

  return (
    <main className="bookmarklet-page">
      <header className="add-head">
        <Mark className="add-mark" />
        <Wordmark className="add-wordmark" />
      </header>
      <h1>Save to Kist bookmarklet</h1>
      <p>
        For browsers without the extension (Safari on Mac, iPhone and iPad, or any browser). It sends the page’s main image (its{' '}
        <code>og:image</code>, or the largest image on the page) to Kist, along with the page address and title. You confirm in a small Kist
        window, signed in as you, so the bookmarklet itself never holds a token.
      </p>
      <label htmlFor={`${id}-kist`}>Your Kist address</label>
      <input id={`${id}-kist`} type="url" value={kist} onChange={(e) => setKist(e.target.value)} placeholder="https://kist.example.com" autoComplete="url" />
      <p>
        {/* the href is set in an effect (javascript: URL) */}
        <a ref={link} className="bookmarklet-link" href="#" onClick={(e) => e.preventDefault()} draggable>
          Save to Kist
        </a>
      </p>
      <ol>
        <li>
          <strong>Mac:</strong> drag the button above to your bookmarks bar (in Safari, show it with View → Show Favorites Bar).
        </li>
        <li>
          <strong>iPhone / iPad:</strong> bookmark this page, then edit that bookmark and replace its address with the text below.
        </li>
        <li>
          On any page, click the bookmark. A Kist window opens with the image; check it and click <strong>Save</strong>.
        </li>
      </ol>
      <label htmlFor={`${id}-code`}>Bookmarklet code</label>
      <textarea id={`${id}-code`} readOnly value={code} rows={4} className="mono" onFocus={(e) => e.currentTarget.select()} />
      <p className="hint">If the window is blocked, the bookmarklet opens Kist in the same tab instead and brings you back afterwards.</p>
    </main>
  )
}
