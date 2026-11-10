// /map (Milestone 5): items with GPS on a MapLibre map, clustered by the
// server. MapLibre and the PMTiles reader are vendored from npm and load
// with this screen only. Tiles come from /api/map/config: the self-hosted
// basemap.pmtiles (pmtiles:// protocol), a hosted XYZ/PMTiles URL, or none
// (a plain background, the points still work). Picking a cluster shows its
// items in the grid (the bbox filter); the list beside the map does the
// same by keyboard.

import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import * as maplibregl from 'maplibre-gl'
import { Protocol } from 'pmtiles'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { api45, type MapCluster, type MapPage } from '../../m45/api45'
import { bboxString } from '../../m45/filters'
import { useAppState } from '../../state'
import { describeError } from '../../writes'

maplibregl.setWorkerUrl(workerUrl)
let protocolAdded = false

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

/** A small style for a Protomaps basemap without labels (labels would need
 * glyph files from elsewhere): land, water, parks, roads, borders. */
function styleFor(tiles: string | null, attribution: string | null): maplibregl.StyleSpecification {
  const bg = cssVar('--bg-hover', '#272725')
  const land = cssVar('--bg-raised', '#1c1c1a')
  const water = cssVar('--bg-active', '#31302d')
  const line = cssVar('--field-border', '#6e6c66')
  const dim = cssVar('--border', '#34332f')
  const layers: maplibregl.LayerSpecification[] = [{ id: 'bg', type: 'background', paint: { 'background-color': tiles ? water : bg } }]
  const sources: maplibregl.StyleSpecification['sources'] = {}
  if (tiles && /\.pmtiles(\?|$)/.test(tiles)) {
    const abs = new URL(tiles, window.location.origin).href
    sources.base = { type: 'vector', url: `pmtiles://${abs}`, attribution: attribution ?? undefined }
    layers.push(
      { id: 'earth', type: 'fill', source: 'base', 'source-layer': 'earth', paint: { 'fill-color': land } },
      { id: 'parks', type: 'fill', source: 'base', 'source-layer': 'landuse', paint: { 'fill-color': dim, 'fill-opacity': 0.5 } },
      { id: 'water', type: 'fill', source: 'base', 'source-layer': 'water', paint: { 'fill-color': water } },
      { id: 'roads', type: 'line', source: 'base', 'source-layer': 'roads', paint: { 'line-color': dim, 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.3, 14, 2] } },
      { id: 'borders', type: 'line', source: 'base', 'source-layer': 'boundaries', paint: { 'line-color': line, 'line-width': 0.8, 'line-dasharray': [2, 2] } },
    )
  } else if (tiles) {
    sources.base = { type: 'raster', tiles: [tiles], tileSize: 256, attribution: attribution ?? undefined }
    layers.push({ id: 'raster', type: 'raster', source: 'base' })
  }
  return { version: 8, sources, layers }
}

/** The visible area as w,s,e,n within ±180 (null: the whole world). */
function viewBbox(map: maplibregl.Map): string | null {
  const b = map.getBounds()
  let w = b.getWest()
  let e = b.getEast()
  if (e - w >= 360) return null
  const wrap = (x: number) => ((((x + 180) % 360) + 360) % 360) - 180
  w = wrap(w)
  e = wrap(e)
  const s = Math.max(-90, b.getSouth())
  const n = Math.min(90, b.getNorth())
  return [w, s, e, n].map((x) => Math.round(x * 1e5) / 1e5).join(',')
}

export default function MapScreen() {
  const { query, setQuery, mobile } = useAppState()
  const box = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])
  const [view, setView] = useState<{ bbox: string | null; zoom: number }>({ bbox: null, zoom: 1 })
  const [failed, setFailed] = useState('')
  const id = useId()
  const cfg = useQuery({ queryKey: ['map-config'], queryFn: api45.mapConfig, staleTime: 600_000 })
  const points = useQuery({
    queryKey: ['map', query, view.bbox, Math.round(view.zoom)],
    queryFn: () => api45.map(query, view.bbox, view.zoom),
    placeholderData: (p) => p,
  })

  const showInGrid = (b: [number, number, number, number] | string) => {
    setQuery({ bbox: typeof b === 'string' ? b : bboxString(b), order: '-taken' })
  }

  // the map, once the tile config is known
  useEffect(() => {
    if (!box.current || !cfg.data || mapRef.current) return
    if (!protocolAdded) {
      const p = new Protocol()
      maplibregl.addProtocol('pmtiles', p.tile)
      protocolAdded = true
    }
    let map: maplibregl.Map
    try {
      map = new maplibregl.Map({
        container: box.current,
        style: styleFor(cfg.data.tiles_url, cfg.data.attribution),
        center: [10, 30],
        zoom: 1,
        attributionControl: cfg.data.tiles_url ? { compact: true } : false,
        dragRotate: false,
        pitchWithRotate: false,
      })
    } catch (e) {
      setFailed(describeError(e))
      return
    }
    map.touchZoomRotate.disableRotation()
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.getCanvas().setAttribute('aria-label', 'Map of items with a location. Arrow keys pan, plus and minus zoom.')
    const update = () => setView({ bbox: viewBbox(map), zoom: map.getZoom() })
    map.on('moveend', update)
    map.on('load', update)
    map.on('error', (e) => {
      const msg = String((e as unknown as { error?: { message?: string } }).error?.message ?? "")
      if (/pmtiles|tile|Failed to fetch/i.test(msg)) setFailed('The map tiles didn’t load; the points still work.')
    })
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [cfg.data])

  // the clusters, as buttons on the map
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    for (const m of markers.current) m.remove()
    markers.current = []
    for (const c of points.data?.clusters ?? []) {
      const el = document.createElement('button')
      el.type = 'button'
      el.className = `map-pin${c.count > 1 ? ' cluster' : ''}`
      el.setAttribute('aria-label', `${c.count} item${c.count === 1 ? '' : 's'} here: show ${c.count === 1 ? 'it' : 'them'}`)
      el.title = `${c.count} item${c.count === 1 ? '' : 's'}`
      if (c.count === 1) {
        const img = document.createElement('img')
        img.src = `/api/items/${c.item_id}/thumb?size=256`
        img.alt = ''
        el.appendChild(img)
      } else el.textContent = c.count > 999 ? `${Math.round(c.count / 1000)}k` : String(c.count)
      el.addEventListener('click', (ev) => {
        ev.stopPropagation()
        showInGrid(c.bbox)
      })
      markers.current.push(new maplibregl.Marker({ element: el }).setLngLat([c.lon, c.lat]).addTo(map))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points.data])

  // first load: fit what there is
  const fitted = useRef(false)
  useEffect(() => {
    const map = mapRef.current
    const cs = points.data?.clusters
    if (!map || !cs?.length || fitted.current) return
    fitted.current = true
    const w = Math.min(...cs.map((c) => c.bbox[0]))
    const s = Math.min(...cs.map((c) => c.bbox[1]))
    const e = Math.max(...cs.map((c) => c.bbox[2]))
    const n = Math.max(...cs.map((c) => c.bbox[3]))
    map.fitBounds([[w, s], [e, n]], { padding: 60, maxZoom: 12, duration: 0 })
  }, [points.data])

  const data: MapPage | undefined = points.data
  return (
    <main className={`grid-area map-screen${mobile ? ' mobile' : ''}`} aria-labelledby={`${id}-h`}>
      <header className="screen-head">
        <h1 id={`${id}-h`}>Map</h1>
        <p className="hint" role="status">
          {data ? `${data.total.toLocaleString()} item${data.total === 1 ? '' : 's'} with a location in view` : 'Loading…'}
          {data?.truncated ? ' (zoom in to see every group)' : ''}
        </p>
        <button type="button" onClick={() => view.bbox && showInGrid(view.bbox)} disabled={!data?.total}>
          Show these in the grid
        </button>
      </header>
      {cfg.data && !cfg.data.tiles_url && (
        <p className="hint map-note">
          No basemap yet: the points are placed on a blank map. Put a Protomaps file at <code>library/maps/basemap.pmtiles</code> or set{' '}
          <code>KIST_MAP_TILES_URL</code> (see docs/maps.md).
        </p>
      )}
      {failed && (
        <p className="hint map-note" role="alert">
          {failed}
        </p>
      )}
      <div className="map-layout">
        <div ref={box} className="map-box" />
        <nav className="map-list" aria-label="Places on the map">
          <h2 className="section-label">Groups in view</h2>
          {points.isError && <p role="alert">{describeError(points.error)}</p>}
          <ul>
            {(data?.clusters ?? []).map((c: MapCluster) => (
              <li key={`${c.lat},${c.lon}`}>
                <button type="button" className="map-list-row" onClick={() => showInGrid(c.bbox)}>
                  <img src={`/api/items/${c.item_id}/thumb?size=256`} alt="" loading="lazy" />
                  <span>
                    {c.count} item{c.count === 1 ? '' : 's'}
                    <span className="hint mono"> {c.lat.toFixed(2)}, {c.lon.toFixed(2)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {data && data.clusters.length === 0 && <p className="hint">No items with a location here.</p>}
        </nav>
      </div>
    </main>
  )
}
