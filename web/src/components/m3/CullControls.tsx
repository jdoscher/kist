// Pick/reject and colour-label controls (info panel, triage, compare).

import { Ban, Check, Flag as FlagIcon } from 'lucide-react'
import type { Item } from '../../api'
import { LABELS, type Flag, type Label } from '../../cull'

export function FlagButtons({
  flag,
  onFlag,
  disabled,
  keys = false,
  size = 'normal',
}: {
  flag: Item['flag']
  onFlag: (f: Flag | null) => void
  disabled?: boolean
  /** show the P / X keys */
  keys?: boolean
  size?: 'normal' | 'large'
}) {
  return (
    <div className={`flag-buttons ${size}`} role="group" aria-label={`Flag: ${flag === 'pick' ? 'pick' : flag === 'reject' ? 'reject' : 'none'}`}>
      <button
        className={`btn-icon flag-pick${flag === 'pick' ? ' on' : ''}`}
        aria-pressed={flag === 'pick'}
        disabled={disabled}
        title={flag === 'pick' ? 'Picked. Press to unflag (U).' : 'Pick it (P)'}
        aria-keyshortcuts={keys ? 'P' : undefined}
        onClick={() => onFlag(flag === 'pick' ? null : 'pick')}
      >
        <Check size={14} aria-hidden="true" />
        Pick{keys && <kbd aria-hidden="true">P</kbd>}
      </button>
      <button
        className={`btn-icon flag-reject${flag === 'reject' ? ' on' : ''}`}
        aria-pressed={flag === 'reject'}
        disabled={disabled}
        title={flag === 'reject' ? 'Rejected. Press to unflag (U).' : 'Reject it (X)'}
        aria-keyshortcuts={keys ? 'X' : undefined}
        onClick={() => onFlag(flag === 'reject' ? null : 'reject')}
      >
        <Ban size={14} aria-hidden="true" />
        Reject{keys && <kbd aria-hidden="true">X</kbd>}
      </button>
    </div>
  )
}

export function LabelPicker({
  label,
  onLabel,
  disabled,
  keys = false,
}: {
  label: Item['label']
  onLabel: (l: Label | null) => void
  disabled?: boolean
  keys?: boolean
}) {
  return (
    <div className="label-picker" role="group" aria-label={`Colour label: ${label ?? 'none'}`}>
      {LABELS.map((l, i) => (
        <button
          key={l.id}
          className={`label-swatch${label === l.id ? ' on' : ''}`}
          style={{ ['--swatch' as string]: l.hex }}
          aria-pressed={label === l.id}
          aria-label={`${l.name} label`}
          title={`${l.name}${keys && i < 4 ? ` (${i + 6})` : ''}${label === l.id ? ': press again to clear' : ''}`}
          disabled={disabled}
          onClick={() => onLabel(label === l.id ? null : l.id)}
        >
          {label === l.id && <Check size={14} aria-hidden="true" />}
        </button>
      ))}
    </div>
  )
}

/** The flag and label on a tile (decorative: the tile's label says it). */
export function CullBadges({ item }: { item: Pick<Item, 'flag' | 'label'> }) {
  const l = LABELS.find((x) => x.id === item.label)
  if (!item.flag && !l) return null
  return (
    <span className="badge cull" aria-hidden="true">
      {item.flag === 'pick' && <FlagIcon size={14} className="pick" />}
      {item.flag === 'reject' && <Ban size={14} className="reject" />}
      {l && <span className="label-dot" style={{ background: l.hex }} />}
    </span>
  )
}

/** "picked, red label" for tile labels and announcements. */
export function cullText(item: Pick<Item, 'flag' | 'label'>): string {
  const parts = []
  if (item.flag === 'pick') parts.push('picked')
  if (item.flag === 'reject') parts.push('rejected')
  const l = LABELS.find((x) => x.id === item.label)
  if (l) parts.push(`${l.name.toLowerCase()} label`)
  return parts.join(', ')
}
