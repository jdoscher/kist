// The board's ••• menu on a phone (design pass B18): List, Export and Share
// in one menu, so the phone's board header is one row (back, name, •••)
// and the zoom floats in a capsule at the bottom (phone.css). The desktop
// bar keeps its own buttons; on a phone phone.css hides them.

import { Download, Ellipsis, LayoutList, Presentation, Share2 } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { api3 } from '../../../creativeApi'

export function PhoneBoardMenu({
  id,
  name,
  listOn,
  onToggleList,
  onShare,
}: {
  id: string
  name: string
  listOn: boolean
  onToggleList: () => void
  /** left out when the token can't share */
  onShare?: () => void
}) {
  const ref = useRef<HTMLDetailsElement>(null)
  const close = () => ref.current?.removeAttribute('open')
  // a tap outside or Escape closes it, like the app's other menus
  useEffect(() => {
    const away = (e: Event) => {
      if (ref.current?.open && !ref.current.contains(e.target as Node)) close()
    }
    window.addEventListener('pointerdown', away)
    return () => window.removeEventListener('pointerdown', away)
  }, [])

  return (
    <details
      ref={ref}
      className="board-menu board-more"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && ref.current?.open) {
          e.stopPropagation()
          close()
          ref.current.querySelector('summary')?.focus()
        }
      }}
    >
      <summary className="icon-btn" aria-label="More: list, export, share" title="List, export, share">
        <Ellipsis size={20} aria-hidden="true" />
      </summary>
      <div className="board-menu-pop" role="group" aria-label="Board">
        <button
          className="btn-icon"
          aria-pressed={listOn}
          onClick={() => {
            onToggleList()
            close()
          }}
        >
          {listOn ? <Presentation size={16} aria-hidden="true" /> : <LayoutList size={16} aria-hidden="true" />}
          {listOn ? 'Show the board' : 'List'}
        </button>
        <a href={api3.exportUrl(id, 'png', 2)} download={`${name}.png`} onClick={close}>
          <Download size={16} aria-hidden="true" /> Export PNG <span className="dim">(2×)</span>
        </a>
        <a href={api3.exportUrl(id, 'pdf', 1)} download={`${name}.pdf`} onClick={close}>
          <Download size={16} aria-hidden="true" /> Export PDF
        </a>
        <a href={api3.exportUrl(id, 'html')} download={`${name}.html`} onClick={close}>
          <Download size={16} aria-hidden="true" /> Export one HTML file
        </a>
        {onShare && (
          <button
            className="btn-icon"
            onClick={() => {
              close()
              onShare()
            }}
          >
            <Share2 size={16} aria-hidden="true" /> Share link…
          </button>
        )}
        <p className="hint">Exports are made from the last saved version.</p>
      </div>
    </details>
  )
}
