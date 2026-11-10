// The AI settings' Milestone 2 cards: the caption provider (with a clear
// warning when images leave the house) and whether faces are on.

import { TriangleAlert } from 'lucide-react'
import type { AiStatus } from '../api'
import { useAppState } from '../state'


export function AiExtras({ data }: { data: AiStatus }) {
  const { openOrganise } = useAppState()
  const c = data.captions
  return (
    <>
      <div className="health-card">
        <h3>Captions</h3>
        {!c || !c.provider ? (
          <p>
            Off. Set <code>KIST_CAPTION_PROVIDER</code> to <code>ollama</code> (a vision model on your own computer;
            nothing leaves the house) to describe images in a sentence you can search and edit.
          </p>
        ) : (
          <>
            {c.leaves_house && (
              <p className="warn-box leaves-house" role="note">
                <TriangleAlert size={16} strokeWidth={1.75} aria-hidden="true" />
                <span>
                  <strong>Images leave your network.</strong> {c.warning}
                </span>
              </p>
            )}
            <dl className="facts">
              <div>
                <dt>Provider</dt>
                <dd>
                  {c.provider}
                  {c.leaves_house ? ' (cloud)' : ' (on your hardware)'}
                </dd>
              </div>
              <div>
                <dt>Model</dt>
                <dd className="mono">{c.model ?? 'default'}</dd>
              </div>
              <div>
                <dt>Runs on</dt>
                <dd>{c.runs_on === 'helper' ? 'a helper (kist helper --kinds caption)' : 'this server'}</dd>
              </div>
              <div>
                <dt>New items</dt>
                <dd>{c.auto ? 'captioned as they arrive' : 'on request (Describe in the info panel)'}</dd>
              </div>
              <div>
                <dt>Captioned</dt>
                <dd>{(data.captioned ?? 0).toLocaleString()} {data.captioned === 1 ? 'item' : 'items'}</dd>
              </div>
            </dl>
          </>
        )}
      </div>
      <div className="health-card">
        <h3>Faces</h3>
        {data.faces ? (
          <>
            <p>On: faces are found and grouped into people, on your own hardware.</p>
            <button onClick={() => openOrganise('people')}>See people</button>
          </>
        ) : (
          <p>
            Off (face data is personal). Set <code>KIST_FACES=1</code> for the app, worker and ml service to find and
            group faces.
          </p>
        )}
      </div>
    </>
  )
}
