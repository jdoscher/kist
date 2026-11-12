// A 3D model in <model-viewer> (Google's web component, vendored from npm
// and loaded only here). "Set poster" renders the current view to a PNG
// and uploads it as the item's thumbnail (the server can't render 3D).

import '@google/model-viewer'
import { Camera, RotateCcw } from 'lucide-react'
import { createElement, useEffect, useRef, useState } from 'react'
import type { Item } from '../../api'
import { useNotices } from '../Notices'
import { describeError } from '../../writes'
import { useQueryClient } from '@tanstack/react-query'
import { useApplyItems, useCaps } from '../../data'
import { api45 } from '../../m45/api45'

interface ModelViewerElement extends HTMLElement {
  toBlob(opts?: { mimeType?: string; qualityArgument?: number; idealAspect?: boolean }): Promise<Blob>
  resetTurntableRotation?: () => void
  cameraOrbit: string
  loaded?: boolean
}

function usePosterUpload(item: Item) {
  const apply = useApplyItems()
  const qc = useQueryClient()
  return async (blob: Blob) => {
    const updated = await api45.poster(item.id, blob)
    apply([updated])
    void qc.invalidateQueries({ queryKey: ['item', item.id] })
    return updated
  }
}

export default function ModelView({ item, src }: { item: Item; src?: string }) {
  const ref = useRef<ModelViewerElement | null>(null)
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading')
  const [busy, setBusy] = useState(false)
  const caps = useCaps()
  const upload = usePosterUpload(item)
  const { notify } = useNotices()
  const model = src ?? `/api/items/${item.id}/model`

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ok = () => setState('ok')
    const bad = () => setState('error')
    el.addEventListener('load', ok)
    el.addEventListener('error', bad)
    return () => {
      el.removeEventListener('load', ok)
      el.removeEventListener('error', bad)
    }
  }, [model])

  const poster = async () => {
    const el = ref.current
    if (!el) return
    setBusy(true)
    try {
      const blob = await el.toBlob({ mimeType: 'image/png', idealAspect: true })
      await upload(blob)
      notify({ message: 'Poster saved: the thumbnail now shows this view.' })
    } catch (e) {
      notify({ message: `Couldn’t save the poster: ${describeError(e)}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="media-stage model-stage">
      {createElement('model-viewer', {
        ref,
        src: model,
        alt: `3D model: ${item.filename}`,
        'camera-controls': '',
        'touch-action': 'pan-y',
        'shadow-intensity': '0.8',
        exposure: '1',
        'interaction-prompt': 'none',
        class: 'model-viewer',
      })}
      <p className="sr-only" role="status">
        {state === 'loading' ? 'Loading the model…' : state === 'error' ? 'The model could not be shown.' : 'Model loaded. Drag or use the arrow keys to turn it.'}
      </p>
      {state === 'error' && (
        <p className="hint">
          This model can’t be shown in the browser ({item.ext.toUpperCase()}). GLB and glTF always work; OBJ, STL and PLY need the server’s trimesh
          converter; FBX isn’t supported.
        </p>
      )}
      <div className="media-tools" role="toolbar" aria-label="3D tools">
        <button
          type="button"
          className="btn-icon"
          onClick={() => {
            if (ref.current) ref.current.cameraOrbit = 'auto auto auto'
          }}
        >
          <RotateCcw size={14} aria-hidden="true" /> Reset view
        </button>
        {caps.canEdit && !src && (
          <button type="button" className="btn-icon" disabled={busy || state !== 'ok'} onClick={() => void poster()} title="Use this view as the thumbnail">
            <Camera size={14} aria-hidden="true" /> {busy ? 'Saving…' : 'Set poster'}
          </button>
        )}
      </div>
    </div>
  )
}
