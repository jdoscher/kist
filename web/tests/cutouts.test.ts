// F7 cutouts: the web logic (no React).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  canCutOut,
  closeCutout,
  cutoutApi,
  cutoutInfo,
  cutoutPreviewUrl,
  cutoutRequest,
  downloadNotice,
  fmtMB,
  isCutout,
  jobText,
  modelChoices,
  openCutout,
  optionErrors,
  subscribeCutout,
  type CutoutStatus,
} from '../src/cutouts.ts'

const status: CutoutStatus = {
  enabled: true,
  available: true,
  reason: null,
  default_model: 'isnet-general-use',
  feather_max: 20,
  models: [
    { name: 'birefnet-lite', label: 'Fine edges', note: 'slower', license: 'MIT', size_bytes: 224005088, state: 'missing', downloaded_bytes: 0, error: null },
    { name: 'isnet-general-use', label: 'Fast', note: 'fast', license: 'Apache-2.0', size_bytes: 178648008, state: 'missing', downloaded_bytes: 0, error: null },
  ],
}

const image = { kind: 'image' as const, mime: 'image/jpeg', preview_status: 'ready' as const, state: 'active' as const, media: null }

test('which items can be cut out', () => {
  assert.equal(canCutOut(image), true)
  assert.equal(canCutOut({ ...image, kind: 'video' }), false)
  assert.equal(canCutOut({ ...image, mime: 'image/svg+xml' }), false)
  assert.equal(canCutOut({ ...image, preview_status: 'pending' }), false)
  assert.equal(canCutOut({ ...image, state: 'trashed' }), false)
  const cut = { ...image, media: { cutout: { source_item: 'A', model: 'isnet-general-use', feather: 0, shadow: false } } }
  assert.equal(isCutout(cut), true)
  assert.equal(canCutOut(cut), false) // a cut-out isn't cut out again
  assert.equal(cutoutInfo(cut)?.source_item, 'A')
  assert.equal(cutoutInfo({ media: { cutout: 'nope' } }), null)
})

test('the first-use download notice names the model and its size', () => {
  assert.match(downloadNotice(status, null)!, /fast model to this server, once \(179 MB, Apache-2.0\)/)
  assert.match(downloadNotice(status, 'birefnet-lite')!, /fine edges model .*224 MB, MIT/)
  const dl = { ...status, models: status.models.map((m) => (m.name === 'isnet-general-use' ? { ...m, state: 'downloading' as const, downloaded_bytes: 50e6 } : m)) }
  assert.match(downloadNotice(dl, null)!, /downloading the fast model \(50 MB of 179 MB\)/)
  const ready = { ...status, models: status.models.map((m) => ({ ...m, state: 'ready' as const })) }
  assert.equal(downloadNotice(ready, null), null)
  assert.equal(downloadNotice(undefined, null), null)
  assert.equal(fmtMB(null), '? MB')
})

test('the default model comes first', () => {
  assert.deepEqual(modelChoices(status).map((m) => m.name), ['isnet-general-use', 'birefnet-lite'])
  assert.deepEqual(modelChoices(undefined), [])
})

test('options and job lines', () => {
  assert.deepEqual(optionErrors({ model: null, feather: 3, shadow: false }), [])
  assert.equal(optionErrors({ model: null, feather: 21, shadow: false }).length, 1)
  assert.equal(optionErrors({ model: null, feather: Number.NaN, shadow: false }).length, 1)
  assert.equal(jobText(undefined), 'Waiting for the worker…')
  assert.match(jobText({ status: 'pending', last_error: 'the ml service answered 503: downloading the cut-out model isnet-general-use (12 of 179 MB); try again shortly' }), /^Downloading the model \(12 of 179 MB\)/)
  assert.match(jobText({ status: 'pending', last_error: "the ml service can't be reached: ConnectError" }), /^Waiting for the ml service \(can't be reached/)
  assert.equal(jobText({ status: 'running', last_error: null }), 'Cutting out the subject…')
  assert.equal(jobText({ status: 'done', last_error: null }), 'The cut-out is ready.')
  assert.equal(jobText({ status: 'failed', last_error: 'no subject' }), 'The cut-out failed: no subject.')
})

test('urls and requests', async () => {
  assert.equal(cutoutPreviewUrl({ id: 'X', preview_rev: 3 }, 2048), '/api/items/X/cutout-preview?size=2048&v=3')
  const calls: { url: string; init: RequestInit }[] = []
  const orig = globalThis.fetch
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({ id: 1, kind: 'cutout', status: 'pending' }), { status: 202, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  try {
    const job = await cutoutApi.one('X', { model: null, feather: 2, shadow: true }, 'web-b1')
    assert.equal(job.kind, 'cutout')
    await cutoutApi.boardNode('B', 'n/1', { model: 'birefnet-lite', feather: 0, shadow: false })
  } finally {
    globalThis.fetch = orig
  }
  assert.equal(calls[0].url, '/api/items/X/cutout')
  assert.equal(calls[0].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].init.body as string), { model: null, feather: 2, shadow: true })
  assert.equal((calls[0].init.headers as Record<string, string>)['X-Kist-Batch'], 'web-b1')
  assert.equal(calls[1].url, '/api/boards/B/nodes/n%2F1/cutout')
})

test('the dialog store tells its host', () => {
  let n = 0
  const off = subscribeCutout(() => n++)
  openCutout({ items: [] })
  assert.deepEqual(cutoutRequest(), { items: [] })
  closeCutout()
  assert.equal(cutoutRequest(), null)
  off()
  openCutout({ items: [] })
  assert.equal(n, 2)
  closeCutout()
})
