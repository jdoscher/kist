// Settings → Kinds (F1 utility collections): the trade packs, each kind on
// or off with its strictness, how many items a kind needs before it shows
// in the sidebar, and the low-res size. Everything here is saved in the
// library (library/.kist/kinds.json), so it survives a rebuild.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { kindsApi, kindsByPack, learningText, strictnessText, type KindInfo, type KindsOverview } from '../../kinds'
import { describeError } from '../../writes'
import { useNotices } from '../Notices'

export default function KindsSettings() {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['kinds'], queryFn: kindsApi.overview })
  const qc = useQueryClient()
  const { notify } = useNotices()
  const id = useId()
  const [busy, setBusy] = useState('')

  const save = async (key: string, run: () => Promise<unknown>) => {
    setBusy(key)
    try {
      const out = await run()
      if (out && typeof out === 'object' && 'kinds' in (out as object)) qc.setQueryData(['kinds'], out as KindsOverview)
      void qc.invalidateQueries({ queryKey: ['kinds'] })
      void qc.invalidateQueries({ queryKey: ['items'], refetchType: 'active' })
    } catch (e) {
      notify({ key: 'kinds', tone: 'error', message: `Couldn't save: ${describeError(e)}` })
    } finally {
      setBusy('')
    }
  }

  return (
    <section aria-labelledby={`${id}-h`} className="kd-settings">
      <div className="settings-section-head">
        <h2 id={`${id}-h`} className="view-title">
          Kinds
        </h2>
      </div>
      <p className="settings-intro">
        Kist sorts pictures into kinds by itself: screenshots, documents, drawings, paintings, camera photos and low-res
        copies. It decides on your own hardware, from what it already knows about each item (the image understanding,
        the text it read, the file’s own data); nothing is sent anywhere. Kinds you correct with <kbd>X</kbd> (not this)
        and <kbd>B</kbd> (belongs here) learn from you after {data?.kinds[0]?.learn_min ?? 10} corrections.
      </p>
      {isLoading && (
        <p className="hint" role="status">
          Loading…
        </p>
      )}
      {isError && <p role="alert">Couldn’t load the kinds: {describeError(error)}</p>}
      {data && !data.enabled && <p className="hint">Kinds are switched off on this server (KIST_KINDS=false).</p>}
      {data && data.enabled && (
        <>
          <div className="health-card">
            <h3>Trade packs</h3>
            <p className="hint">Extra kinds for one line of work. Switch a pack on and Kist sorts the library again in the background.</p>
            <div className="kd-packs">
              {data.packs
                .filter((p) => p.id !== 'base')
                .map((p) => (
                  <label key={p.id} className="kd-check">
                    <input
                      type="checkbox"
                      checked={p.enabled}
                      disabled={busy === `pack:${p.id}`}
                      onChange={(e) => void save(`pack:${p.id}`, () => kindsApi.patch({ packs: { [p.id]: e.target.checked } }))}
                    />
                    {p.name}
                  </label>
                ))}
            </div>
          </div>
          <div className="health-card">
            <h3>Sidebar and size</h3>
            <div className="form-grid">
              <label htmlFor={`${id}-min`}>Show a kind once it has</label>
              <span className="kd-inline">
                <input
                  id={`${id}-min`}
                  type="number"
                  min={0}
                  max={100000}
                  defaultValue={data.min_items}
                  key={`min-${data.min_items}`}
                  onBlur={(e) => {
                    const v = Number(e.target.value)
                    if (Number.isFinite(v) && v !== data.min_items) void save('min', () => kindsApi.patch({ min_items: Math.max(0, Math.round(v)) }))
                  }}
                />
                items
              </span>
              <label htmlFor={`${id}-lowres`}>Low-res: long edge under</label>
              <span className="kd-inline">
                <input
                  id={`${id}-lowres`}
                  type="number"
                  min={16}
                  max={100000}
                  defaultValue={data.low_res_px}
                  key={`lr-${data.low_res_px}`}
                  onBlur={(e) => {
                    const v = Number(e.target.value)
                    if (Number.isFinite(v) && v !== data.low_res_px) void save('lowres', () => kindsApi.patch({ low_res_px: Math.max(16, Math.round(v)) }))
                  }}
                />
                px <span className="kd-dim">(≈ {(data.low_res_px / 300).toFixed(1)} in at 300 dpi)</span>
              </span>
            </div>
            {data.pending_jobs > 0 && (
              <p className="hint" role="status">
                Sorting in the background…
              </p>
            )}
          </div>
          {kindsByPack(data).map(({ pack, kinds }) =>
            kinds.length ? (
              <div className="health-card" key={pack.id}>
                <h3>
                  {pack.id === 'base' ? 'For everyone' : pack.name}
                  {pack.id !== 'base' && !pack.enabled && <span className="kd-dim"> · pack off</span>}
                </h3>
                <ul className="kd-list">
                  {kinds.map((k) => (
                    <KindRow key={k.id} k={k} busy={busy === k.id} onSave={(body) => void save(k.id, () => kindsApi.patchKind(k.id, body))} />
                  ))}
                </ul>
              </div>
            ) : null,
          )}
        </>
      )}
    </section>
  )
}

function KindRow({ k, busy, onSave }: { k: KindInfo; busy: boolean; onSave: (body: { enabled?: boolean | null; strictness?: number }) => void }) {
  const id = useId()
  const [strict, setStrict] = useState(k.strictness)
  return (
    <li className={`kd-kind${k.parent ? ' kd-child' : ''}`}>
      <label className="kd-check" title={k.how}>
        <input type="checkbox" checked={k.enabled} disabled={busy} onChange={(e) => onSave({ enabled: e.target.checked })} />
        <span>
          {k.name}
          {k.opt_in && <span className="kd-dim"> · opt-in</span>}
        </span>
      </label>
      <span className="kd-dim kd-count">{k.enabled ? `${k.count.toLocaleString()} items` : 'off'}</span>
      {k.clip ? (
        <span className="kd-strict">
          <label htmlFor={`${id}-s`} className="sr-only">
            Strictness of {k.name}
          </label>
          <input
            id={`${id}-s`}
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={strict}
            disabled={!k.enabled}
            aria-valuetext={strictnessText(strict)}
            onChange={(e) => setStrict(Number(e.target.value))}
            onPointerUp={() => strict !== k.strictness && onSave({ strictness: strict })}
            onKeyUp={() => strict !== k.strictness && onSave({ strictness: strict })}
          />
          <span className="kd-dim">{strictnessText(strict)}</span>
        </span>
      ) : (
        <span className="kd-dim">Rule</span>
      )}
      <span className="kd-dim kd-how">
        {k.how}
        {k.enabled ? ` · ${learningText(k)}` : ''}
      </span>
      {k.enabled_override !== null && (
        <button className="link-btn" onClick={() => onSave({ enabled: null })} title="Follow the pack again">
          Reset
        </button>
      )}
    </li>
  )
}
