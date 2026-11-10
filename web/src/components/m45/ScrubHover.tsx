// Hover-scrub on video and animated GIF/WebP tiles (Milestone 5): moving
// the pointer across the tile steps through the 20-frame sprite the server
// made (scrub.webp + its layout in item.media.scrub). Desktop pointers
// only; on a phone a tap opens the item as before. The sprite loads on the
// first hover, never with the grid.

import { memo, useState } from 'react'
import type { Item } from '../../api'
import { scrubFrameIndex, scrubFrameStyle, scrubOf } from '../../m45/media'

const canHover = () => typeof window !== 'undefined' && !!window.matchMedia?.('(hover: hover) and (pointer: fine)').matches

export const ScrubHover = memo(function ScrubHover({ item, width, height }: { item: Item; width: number; height: number }) {
  const info = scrubOf(item.media)
  const [frame, setFrame] = useState<number | null>(null)
  if (!info || !canHover()) return null
  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    setFrame(scrubFrameIndex(e.clientX - r.left, r.width, info.frames))
  }
  return (
    <div className="scrub-hover" aria-hidden="true" onMouseMove={onMove} onMouseLeave={() => setFrame(null)}>
      {frame !== null && (
        <>
          <div
            className="scrub-frame"
            style={{ backgroundImage: `url(/api/items/${item.id}/scrub?v=${item.preview_rev})`, ...scrubFrameStyle(info, frame, width, height) }}
          />
          <div className="scrub-bar" style={{ width: `${((frame + 1) / info.frames) * 100}%` }} />
        </>
      )}
    </div>
  )
})
