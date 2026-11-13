// Present (Kist 2.0 F2): pair a phone as the remote. A QR code of
// /present/remote/<id>#<token> (the token never reaches a server log), the
// state of the pairing, and Unpair (the token stops working at once).

import qrcode from 'qrcode-generator'
import { Smartphone, X } from 'lucide-react'
import { useMemo } from 'react'
import { remoteUrl } from '../../present'

export function qrPath(text: string): { size: number; d: string } {
  const qr = qrcode(0, 'M')
  qr.addData(text)
  qr.make()
  const n = qr.getModuleCount()
  let d = ''
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + 4} ${r + 4}h1v1h-1z`
  return { size: n + 8, d }
}

export function QrCode({ text, label }: { text: string; label: string }) {
  const { size, d } = useMemo(() => qrPath(text), [text])
  return (
    <svg className="present-qr" data-url={text} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label} shapeRendering="crispEdges">
      <rect width={size} height={size} fill="#ffffff" />
      <path d={d} fill="#000000" />
    </svg>
  )
}

export interface PairState {
  path: string | null
  connected: boolean
  expiresAt: string | null
  error: string
  busy: boolean
}

export function PairPanel({
  pair,
  onPair,
  onUnpair,
  onClose,
}: {
  pair: PairState
  onPair: () => void
  onUnpair: () => void
  onClose: () => void
}) {
  const url = pair.path ? remoteUrl(window.location.origin, pair.path) : null
  const until = pair.expiresAt ? new Date(pair.expiresAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
  return (
    <div className="present-pair float-surface" role="dialog" aria-label="Phone remote" onClick={(e) => e.stopPropagation()}>
      <div className="present-pair-head">
        <Smartphone size={16} aria-hidden="true" />
        <span className="present-pair-title">Phone remote</span>
        <button type="button" className="icon-btn" aria-label="Close" title="Close (Esc)" onClick={onClose}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      {url ? (
        <>
          <QrCode text={url} label="QR code that opens the remote on your phone" />
          <p className="present-pair-status" role="status">
            {pair.connected ? 'Phone connected.' : 'Scan it with the phone’s camera. It works on the same network or through your Kist address.'}
          </p>
          <p className="hint">
            Works for this show until {until} or until you unpair. Anyone who scans it can move the slides, so pair before the screen is shared.
          </p>
          <div className="present-pair-actions">
            <button type="button" onClick={() => void navigator.clipboard?.writeText(url).catch(() => undefined)} title="Copy the link, to send to the phone another way">
              Copy link
            </button>
            <button type="button" onClick={onPair} disabled={pair.busy} title="A new code; the old one stops working">
              New code
            </button>
            <button type="button" onClick={onUnpair} disabled={pair.busy}>
              Unpair
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="hint">Use your phone to move through the show and mark picks. It talks to this Kist only, nothing outside.</p>
          {pair.error && (
            <p className="error" role="alert">
              {pair.error}
            </p>
          )}
          <button type="button" className="btn-primary" onClick={onPair} disabled={pair.busy}>
            {pair.busy ? 'Pairing…' : 'Show the QR code'}
          </button>
        </>
      )}
    </div>
  )
}
