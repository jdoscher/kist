// Milestones 4 and 5 web logic (no React): scrubbing, clips, share links,
// provenance edits, timeline labels, the /add page's parameters, the
// bookmarklet generator, the new filters and export presets.
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { test } from 'node:test'
import { emptyQuery, itemsParams } from '../src/api.ts'
import { addErrorText, addPayload, BOOKMARKLET_SRC, bookmarkletFor, hostOf, normalizeKistBase, parseAddParams, plainText, safeHttpUrl } from '../src/m45/add.ts'
import { bboxString, cleanFilter, clearedM45, filterChipLabel, m45Filters, parseBbox } from '../src/m45/filters.ts'
import { clipErrors, fmtLatLon, fmtTime, mediaGps, mediaRows, parseTime, scrubFrameIndex, scrubFrameStyle, scrubFrameTime, scrubOf, shutterText, svgColors, waveformBars } from '../src/m45/media.ts'
import { formToPreset, presetErrors, presetSummary, presetToForm } from '../src/m45/presets.ts'
import { aiChoice, aiValue, digitalSourceText, provenanceDiff, provenanceErrors, sourceStatusText } from '../src/m45/provenance.ts'
import { defaultShareForm, shareFormErrors, shareLink, sharePayload, shareStatusText } from '../src/m45/share.ts'
import { barPct, bucketLabel, bucketRange, bucketSection, groupBuckets, yearsAgoText } from '../src/m45/timeline.ts'
import { smartFolderQuery } from '../src/query.ts'
import { fromUrl, toUrl } from '../src/url.ts'

// ---------- scrub ----------

test('the scrub frame follows the pointer across the tile', () => {
  assert.equal(scrubFrameIndex(0, 200, 20), 0)
  assert.equal(scrubFrameIndex(9.99, 200, 20), 0)
  assert.equal(scrubFrameIndex(10, 200, 20), 1)
  assert.equal(scrubFrameIndex(100, 200, 20), 10)
  assert.equal(scrubFrameIndex(199.9, 200, 20), 19)
  // past either edge, and degenerate sizes, stay in range
  assert.equal(scrubFrameIndex(-30, 200, 20), 0)
  assert.equal(scrubFrameIndex(500, 200, 20), 19)
  assert.equal(scrubFrameIndex(50, 0, 20), 0)
  assert.equal(scrubFrameIndex(50, 200, 1), 0)
  assert.equal(scrubFrameIndex(Number.NaN, 200, 20), 0)
})

test('a sprite frame is positioned to cover the tile', () => {
  const info = { frames: 20, cols: 5, rows: 4, frame_w: 256, frame_h: 144 }
  // a 256x144 tile shows frames 1:1
  const s0 = scrubFrameStyle(info, 0, 256, 144)
  assert.equal(s0.backgroundSize, '1280px 576px')
  assert.equal(s0.backgroundPosition, '0px 0px')
  const s7 = scrubFrameStyle(info, 7, 256, 144) // col 2, row 1
  assert.equal(s7.backgroundPosition, '-512px -144px')
  // a square tile scales up to cover and centres the frame
  const sq = scrubFrameStyle(info, 0, 288, 288)
  assert.equal(sq.backgroundSize, '2560px 1152px')
  assert.equal(sq.backgroundPosition, '-112px 0px')
  assert.equal(scrubFrameTime({ ...info, times: [0.2, 0.6] }, 1, 8), 0.6)
  assert.equal(scrubFrameTime(info, 0, 8), 0.2)
  assert.equal(scrubFrameTime(info, 0, null), null)
  assert.deepEqual(scrubOf({ scrub: info }), info)
  assert.equal(scrubOf({ scrub: { frames: 3 } }), null)
  assert.equal(scrubOf(null), null)
})

// ---------- clips ----------

test('clip ranges are checked before a job is queued', () => {
  assert.deepEqual(clipErrors(1, 4, 8, 'mp4'), [])
  assert.match(clipErrors(4, 4, 8, 'mp4')[0], /after the start/)
  assert.match(clipErrors(5, 2, 8, 'mp4')[0], /after the start/)
  assert.match(clipErrors(-1, 2, 8, 'mp4')[0], /before 0/)
  assert.match(clipErrors(1, 9, 8, 'mp4')[0], /past the end/)
  assert.deepEqual(clipErrors(1, 8.04, 8, 'mp4'), []) // rounding slack
  assert.match(clipErrors(1, 1.05, 8, 'wav')[0], /tenth/)
  assert.match(clipErrors(0, 31, 60, 'gif')[0], /at most 30/)
  assert.deepEqual(clipErrors(0, 31, 60, 'mp4'), [])
  assert.deepEqual(clipErrors(0, 31, null, 'mp3'), [])
  assert.match(clipErrors(Number.NaN, 2, 8, 'mp4')[0], /numbers/)
})

test('times read and print as m:ss.s', () => {
  assert.equal(fmtTime(0), '0:00.0')
  assert.equal(fmtTime(62.5), '1:02.5')
  assert.equal(fmtTime(3723), '1:02:03.0')
  assert.equal(fmtTime(-5), '0:00.0')
  assert.equal(parseTime('1:02.5'), 62.5)
  assert.equal(parseTime('62.5'), 62.5)
  assert.equal(parseTime('1:02:03'), 3723)
  assert.equal(parseTime(fmtTime(12.3)), 12.3)
  assert.equal(parseTime(''), null)
  assert.equal(parseTime('a:b'), null)
  assert.equal(parseTime('-3'), null)
})

// ---------- share links ----------

test('the share form becomes the POST /api/shares body', () => {
  const f = defaultShareForm('folder', 'F1', [], 'Kyoto')
  assert.equal(f.expiry, '30') // the server's default too
  assert.deepEqual(sharePayload(f), {
    target_kind: 'folder',
    target_id: 'F1',
    title: 'Kyoto',
    allow_download: false,
    allow_original: false,
    expires_in_days: 30,
  })
  // the original needs downloads on
  assert.equal(sharePayload({ ...f, allowOriginal: true }).allow_original, false)
  assert.equal(sharePayload({ ...f, allowDownload: true, allowOriginal: true }).allow_original, true)
  // a password only when asked for; never expiring is 0 days
  assert.equal(sharePayload({ ...f, password: 'secret' }).password, undefined)
  assert.equal(sharePayload({ ...f, usePassword: true, password: 'secret' }).password, 'secret')
  assert.equal(sharePayload({ ...f, expiry: 'never' }).expires_in_days, 0)
  const dated = sharePayload({ ...f, expiry: 'date', date: '2030-01-02' })
  assert.equal(dated.expires_in_days, undefined)
  assert.equal(new Date(dated.expires_at!).getDate(), 2)
  // items: de-duplicated ids, no target_id
  const items = sharePayload(defaultShareForm('items', null, ['a', 'b', 'a']))
  assert.deepEqual(items.item_ids, ['a', 'b'])
  assert.equal(items.target_id, undefined)
})

test('share form errors', () => {
  const now = new Date('2026-11-09T12:00:00')
  const f = defaultShareForm('board', 'B1')
  assert.deepEqual(shareFormErrors(f, now), [])
  assert.match(shareFormErrors({ ...f, targetId: null }, now)[0], /Nothing/)
  assert.match(shareFormErrors(defaultShareForm('items', null, []), now)[0], /Nothing/)
  assert.match(shareFormErrors({ ...f, usePassword: true, password: 'abc' }, now)[0], /4 characters/)
  assert.match(shareFormErrors({ ...f, expiry: 'date', date: '' }, now)[0], /Pick the day/)
  assert.match(shareFormErrors({ ...f, expiry: 'date', date: '2026-11-08' }, now)[0], /passed/)
  assert.deepEqual(shareFormErrors({ ...f, expiry: 'date', date: '2026-11-09' }, now), []) // until the end of today
  assert.match(shareFormErrors({ ...f, title: 'x'.repeat(201) }, now)[0], /200/)
})

test('share links and status text', () => {
  assert.equal(shareLink({ url: 'https://kist.example.com/s/abc', slug: 'abc' }, 'http://x'), 'https://kist.example.com/s/abc')
  assert.equal(shareLink({ url: '/s/abc', slug: 'abc' }, 'http://localhost:8775/'), 'http://localhost:8775/s/abc')
  const now = new Date('2026-11-09T00:00:00Z')
  assert.equal(shareStatusText({ status: 'active', expires_at: null, revoked_at: null }, now), 'Never expires')
  assert.equal(shareStatusText({ status: 'active', expires_at: '2026-11-19T00:00:00Z', revoked_at: null }, now), 'Expires in 10 days')
  assert.equal(shareStatusText({ status: 'active', expires_at: '2026-11-09T05:00:00Z', revoked_at: null }, now), 'Expires within a day')
  assert.equal(shareStatusText({ status: 'revoked', expires_at: null, revoked_at: 'x' }, now), 'Revoked')
  assert.equal(shareStatusText({ status: 'expired', expires_at: '2026-01-01', revoked_at: null }, now), 'Expired')
})

// ---------- provenance ----------

test('a provenance edit sends only what changed, null for cleared fields', () => {
  const before = { page_url: 'https://a.example/p', credit: 'A', license: 'CC BY 4.0', method: 'extension' }
  assert.deepEqual(provenanceDiff(before, { page_url: 'https://a.example/p', credit: 'A', license: 'CC BY 4.0' }), {})
  assert.deepEqual(provenanceDiff(before, { page_url: 'https://a.example/p', credit: ' B ', license: '' }), { credit: 'B', license: null })
  assert.deepEqual(provenanceDiff(before, { creator: 'Jay' }), { creator: 'Jay' })
  assert.deepEqual(provenanceDiff(null, { creator: '  ' }), {})
  // the AI flag: true / false / null (unknown)
  assert.deepEqual(provenanceDiff({ ai_generated: null }, { ai_generated: true }), { ai_generated: true })
  assert.deepEqual(provenanceDiff({ ai_generated: true }, { ai_generated: null }), { ai_generated: null })
  assert.deepEqual(provenanceDiff({}, { ai_generated: null }), {})
  assert.equal(aiChoice({ ai_generated: true }), 'yes')
  assert.equal(aiChoice({ ai_generated: false }), 'no')
  assert.equal(aiChoice({}), 'unknown')
  assert.equal(aiValue('unknown'), null)
  assert.deepEqual(provenanceErrors({ page_url: 'javascript:alert(1)', rights_url: 'https://ok.example', image_url: '' }), {
    page_url: 'An address starting with http:// or https://',
  })
  assert.equal(digitalSourceText('http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia'), 'Made by generative AI')
  assert.equal(digitalSourceText(null), null)
  assert.equal(sourceStatusText('gone'), 'Gone')
  assert.equal(sourceStatusText(null), 'Not checked yet')
})

// ---------- timeline ----------

test('timeline buckets: labels, sections, ranges', () => {
  assert.equal(bucketLabel('2024'), '2024')
  assert.equal(bucketLabel('2024-06'), 'June 2024')
  assert.equal(bucketLabel('2024-06-21'), 'Friday 21 June 2024')
  assert.equal(bucketLabel('nonsense'), 'nonsense')
  assert.equal(bucketSection('2024-06'), '2024')
  assert.equal(bucketSection('2024-06-21'), 'June 2024')
  assert.equal(bucketSection('2024'), '')
  assert.deepEqual(bucketRange('2024'), { after: '2024-01-01', before: '2024-12-31' })
  assert.deepEqual(bucketRange('2024-02'), { after: '2024-02-01', before: '2024-02-29' })
  assert.deepEqual(bucketRange('2023-02'), { after: '2023-02-01', before: '2023-02-28' })
  assert.deepEqual(bucketRange('2024-06-21'), { after: '2024-06-21', before: '2024-06-21' })
  assert.equal(bucketRange('x'), null)
  const g = groupBuckets([
    { key: '2026-11', count: 9, first_item_id: 'a' },
    { key: '2026-01', count: 1, first_item_id: 'b' },
    { key: '2025-03', count: 2, first_item_id: 'c' },
  ])
  assert.deepEqual(g.map((s) => [s.section, s.total, s.buckets.length]), [['2026', 10, 2], ['2025', 2, 1]])
  assert.equal(yearsAgoText(1), 'A year ago')
  assert.equal(yearsAgoText(3), '3 years ago')
  assert.equal(barPct(5, 10), 50)
  assert.equal(barPct(1, 1000), 2)
  assert.equal(barPct(0, 10), 0)
})

// ---------- /add and the bookmarklet ----------

test('/add reads its parameters safely', () => {
  const qs = new URLSearchParams()
  qs.set('url', 'https://img.example.com/a.jpg')
  qs.append('alt', 'https://img.example.com/b.jpg')
  qs.append('alt', 'javascript:alert(1)')
  qs.append('alt', 'https://img.example.com/a.jpg') // the main one again
  qs.append('alt', 'data:image/png;base64,AAA')
  for (const n of ['c', 'd', 'e', 'f']) qs.append('alt', `https://img.example.com/${n}.jpg`)
  qs.set('page', 'https://www.example.com/post?id=1')
  qs.set('title', '<img src=x onerror=alert(1)>\u0000 Title')
  qs.set('v', '1')
  const p = parseAddParams(`?${qs}`)
  assert.equal(p.url, 'https://img.example.com/a.jpg')
  assert.deepEqual(p.alts, ['https://img.example.com/b.jpg', 'https://img.example.com/c.jpg', 'https://img.example.com/d.jpg', 'https://img.example.com/e.jpg'])
  assert.equal(p.page, 'https://www.example.com/post?id=1')
  assert.equal(p.title, '<img src=x onerror=alert(1)>  Title') // text, rendered as text
  assert.equal(p.returnToPage, false)
  assert.equal(p.version, '1')
  assert.equal(hostOf(p.page), 'example.com')
  // refused: other schemes, over-long values, no url at all
  assert.equal(parseAddParams('?url=javascript:alert(1)').url, null)
  assert.equal(parseAddParams('?url=file:///etc/passwd').url, null)
  assert.equal(parseAddParams(`?url=https://a.example/${'x'.repeat(2000)}`).url, null)
  assert.equal(parseAddParams(`?url=https://a.example/x.jpg&title=${'t'.repeat(301)}`).title, '')
  assert.equal(parseAddParams('').url, null)
  assert.equal(parseAddParams('?url=https://a.example/x.jpg&return=1&v=9').returnToPage, true)
  assert.equal(safeHttpUrl('HTTPS://A.example/x'), 'https://a.example/x')
  assert.equal(safeHttpUrl('https://'), null)
  assert.equal(plainText('a\nb'), 'a b')
})

test('/add saves with bookmarklet provenance', () => {
  const p = parseAddParams('?url=https://img.example.com/a.jpg&page=https://example.com/p&title=Hello')
  const body = addPayload(p, 'https://img.example.com/b.jpg', { tags: ['x'], folderIds: ['F'], notes: ' n ' }, new Date('2026-11-09T10:00:00Z'))
  assert.deepEqual(body, {
    url: 'https://img.example.com/b.jpg',
    image_url: 'https://img.example.com/b.jpg',
    captured_at: '2026-11-09T10:00:00.000Z',
    method: 'bookmarklet',
    source_url: 'https://example.com/p',
    page_url: 'https://example.com/p',
    title: 'Hello',
    tags: ['x'],
    folder_ids: ['F'],
    notes: 'n',
  })
  const bare = addPayload(parseAddParams('?url=https://img.example.com/a.jpg'), 'https://img.example.com/a.jpg', { tags: [], folderIds: [], notes: '' })
  assert.equal('page_url' in bare || 'tags' in bare || 'notes' in bare, false)
  assert.match(addErrorText(422, 'the site refused it'), /couldn’t download that image: the site refused it\. Save it with the browser extension/)
  assert.match(addErrorText(413, ''), /larger/)
})

test('the bookmarklet generator', () => {
  assert.equal(normalizeKistBase('kist.example.com/'), 'https://kist.example.com')
  assert.equal(normalizeKistBase('http://nas.local:8080/kist/'), 'http://nas.local:8080/kist')
  assert.equal(normalizeKistBase(''), '')
  assert.equal(normalizeKistBase('http://'), '')
  const js = bookmarkletFor('https://kist.example.com/')
  assert.ok(js.startsWith('javascript:'))
  const code = decodeURIComponent(js.slice('javascript:'.length))
  assert.ok(code.endsWith('})("https://kist.example.com");'))
  assert.ok(!code.includes('__KIST__'))
  assert.match(code, /K \+ "\/add\?" \+ q/)
  assert.match(code, /&return=1/)
  assert.equal(bookmarkletFor('  '), '')
  // a quote in the address can't break out of the string literal
  assert.ok(decodeURIComponent(bookmarkletFor('https://a.example/"x').slice(11)).includes('"https://a.example/%22x"'))
  // the same source as the extension repo's generator page, when it's here
  const ext = new URL('../../../kist-ext/docs/bookmarklet.html', import.meta.url)
  if (existsSync(ext)) {
    const html = readFileSync(ext, 'utf8')
    const m = /<script type="text\/plain" id="src">([\s\S]*?)<\/script>/.exec(html)
    assert.ok(m, 'bookmarklet.html embeds its source')
    assert.equal(m![1], BOOKMARKLET_SRC)
  }
})

// ---------- filters ----------

test('the M4/M5 filters ride in the query, the URL and the request', () => {
  assert.equal(cleanFilter('has_gps', '1'), 'true')
  assert.equal(cleanFilter('has_gps', 'maybe'), null)
  assert.equal(cleanFilter('duration_min', '-3'), null)
  assert.equal(cleanFilter('duration_min', '2.5'), '2.5')
  assert.equal(cleanFilter('taken_after', '2024-01-01'), '2024-01-01')
  assert.equal(cleanFilter('taken_after', '2024-01-01T00:00:00Z'), '2024-01-01T00:00:00Z')
  assert.equal(cleanFilter('taken_after', 'yesterday'), null)
  assert.equal(cleanFilter('source_status', 'gone'), 'gone')
  assert.equal(cleanFilter('source_status', 'lost'), null)
  assert.equal(cleanFilter('ai_generated', 'unknown'), 'unknown')
  assert.equal(cleanFilter('bbox', '1,2,3'), null)
  assert.deepEqual(parseBbox('-10,35,30,60'), [-10, 35, 30, 60])
  assert.equal(parseBbox('0,10,5,5'), null)
  assert.equal(bboxString([135.7681, 34.9949, 135.785, 35.0116]), '135.7676,34.9944,135.7855,35.0121')
  assert.equal(bboxString([179.9999, -90, 180, 90]), '179.9994,-90,180,90')

  const q = { ...emptyQuery, camera: 'X100', has_gps: 'true', bbox: '-10,35,30,60', source_status: 'gone', place: null }
  assert.deepEqual(m45Filters(q), { camera: 'X100', has_gps: 'true', bbox: '-10,35,30,60', source_status: 'gone' })
  const p = itemsParams(q)
  assert.equal(p.get('camera'), 'X100')
  assert.equal(p.get('has_gps'), 'true')
  assert.equal(p.get('bbox'), '-10,35,30,60')
  assert.equal(p.get('place'), null)
  // the URL round-trips them; smart folders save them
  const url = toUrl({ query: q, smartId: null, item: null, full: false })
  assert.match(url, /camera=X100/)
  const back = fromUrl(url.split('?')[0], `?${url.split('?')[1]}`)
  assert.equal(back.query.camera, 'X100')
  assert.equal(back.query.bbox, '-10,35,30,60')
  assert.equal(fromUrl('/all', '?has_gps=sometimes').query.has_gps, undefined)
  assert.equal(smartFolderQuery(q).camera, 'X100')
  assert.equal(Object.values(clearedM45()).every((v) => v === null), true)
  assert.equal(filterChipLabel('has_gps', 'false'), 'No location')
  assert.equal(filterChipLabel('license', 'none'), 'No licence')
})

test('timeline and map screens keep the library filters in their URL', () => {
  const t = fromUrl('/timeline', '?group=day&camera=Sony&type=image')
  assert.deepEqual(t.screen, { kind: 'timeline', group: 'day' })
  assert.equal(t.query.camera, 'Sony')
  assert.equal(t.query.type, 'image')
  assert.equal(toUrl(t), '/timeline?type=image&camera=Sony&group=day')
  assert.deepEqual(fromUrl('/timeline', '?group=week').screen, { kind: 'timeline', group: 'month' })
  assert.equal(toUrl(fromUrl('/map', '')), '/map')
  const add = fromUrl('/add', '?url=https%3A%2F%2Fa.example%2Fx.jpg&v=1')
  assert.deepEqual(add.screen, { kind: 'add', search: '?url=https%3A%2F%2Fa.example%2Fx.jpg&v=1' })
  // the /add URL is kept as it came (the sign-in screen returns to it)
  assert.equal(toUrl(add), '/add?url=https%3A%2F%2Fa.example%2Fx.jpg&v=1')
  assert.equal(fromUrl('/settings/sharing', '').settings, 'sharing')
  assert.equal(fromUrl('/settings/import', '').settings, 'import')
  assert.equal(fromUrl('/settings/phone', '').settings, 'phone')
})

// ---------- media facts ----------

test('media details read the media block', () => {
  const media = {
    exif: { camera: 'FUJIFILM X100V', lens: '23mm F2', focal_length: 23, aperture: 2.8, shutter: 0.004, shutter_text: '1/250', iso: 400, lat: 35.0116, lon: 135.7681 },
  }
  const rows = Object.fromEntries(mediaRows(media))
  assert.equal(rows.Camera, 'FUJIFILM X100V')
  assert.equal(rows.Exposure, '23 mm · ƒ/2.8 · 1/250 s · ISO 400')
  assert.deepEqual(mediaGps(media), { lat: 35.0116, lon: 135.7681 })
  assert.equal(fmtLatLon(35.0116, -9.1394), '35.0116° N, 9.1394° W')
  const video = Object.fromEntries(mediaRows({ video: { duration: 8, fps: 25, codec: 'h264', audio_codec: 'aac', bitrate: 847735, has_audio: true } }))
  assert.deepEqual(video, { Duration: '0:08.0', 'Frame rate': '25 fps', Codec: 'h264 + aac', 'Bit rate': '848 kb/s', Sound: 'Yes' })
  const audio = Object.fromEntries(mediaRows({ audio: { duration: 6, audio_codec: 'mp3', artist: 'Kist Test', title: 'Sine' } }))
  assert.equal(audio.Artist, 'Kist Test')
  assert.equal('Sound' in audio, false)
  assert.equal(Object.fromEntries(mediaRows({ model: { triangles: 12, size: [1, 1, 1], format: 'glb' } })).Triangles, '12')
  assert.deepEqual(mediaRows(null), [])
  assert.equal(mediaGps({ exif: { lat: 1 } }), null)
  assert.equal(shutterText(2), '2')
  assert.equal(shutterText(0.0125), '1/80')
  assert.deepEqual(svgColors({ svg: { colors: [{ hex: '#1d3557' }, { hex: 'url(#x)' }] } }), ['#1d3557'])
})

test('waveform peaks become bar heights', () => {
  // 8-bit min/max pairs
  const data = [-64, 64, -128, 127, 0, 0, -32, 32]
  assert.deepEqual(waveformBars(data, 8, 4), [0.5, 1, 0, 0.25])
  assert.deepEqual(waveformBars(data, 8, 2), [1, 0.25])
  assert.deepEqual(waveformBars([], 8, 10), [])
  assert.equal(waveformBars([-16384, 16384], 16, 1)[0], 0.5)
})

// ---------- export presets ----------

test('export presets: form <-> preset', () => {
  const p = { name: 'client-proof', max_side: 1600, format: 'jpg' as const, quality: 80, strip_metadata: true, watermark: { text: 'PROOF', position: 'center', opacity: 0.35 } }
  const f = presetToForm(p)
  assert.equal(f.maxSide, '1600')
  assert.equal(f.watermark, true)
  assert.deepEqual(formToPreset(f, true), { ...p, contact_sheet: null })
  assert.deepEqual(presetErrors(f), [])
  assert.match(presetErrors({ ...f, maxSide: '8' })[0], /16 to 16384/)
  assert.match(presetErrors({ ...f, wmText: ' ' })[0], /watermark text/)
  assert.match(presetErrors({ ...f, contactSheet: true, cols: 0 })[0], /1 to 12/)
  assert.equal(formToPreset({ ...f, maxSide: '' }).max_side, null)
  assert.equal(presetSummary(p), '1600 px · JPG · quality 80 · watermark “PROOF” · metadata removed')
  assert.equal(presetSummary({ contact_sheet: { cols: 4, caption: 'filename' }, strip_metadata: true }), 'contact sheet, 4 across (PDF) · metadata removed')
})
