// The search box's modes, Auto's guess, and the match snippets.
// Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { emptyQuery, type Query } from '../src/api.ts'
import { firstImage, imageFor, registerImage } from '../src/imageSearch.ts'
import { emptyState, hasNarrowing, normalizeSaved, smartFolderQuery } from '../src/query.ts'
import {
  buildSearch,
  tagTokens,
  colourSearch,
  hexColor,
  isRanked,
  looksLikeKeyword,
  matchLabel,
  rankedErrorText,
  showMatchBadge,
  modeOf,
  moreLikeThis,
  parseSnippet,
  searchText,
  searchTitle,
  snippetText,
} from '../src/search.ts'

const q = (p: Partial<Query>): Query => ({ ...emptyQuery, ...p })

test('Auto searches names and codes as words, descriptions by meaning too', () => {
  for (const kw of ['IMG_2231', 'DSC0042', 'receipt.pdf', '"zero trust"', 'v2', '2026', 'jay@doscher.com', 'a/b', 'kist.app'])
    assert.equal(looksLikeKeyword(kw), true, kw)
  for (const d of ['dog', 'brown suede boots', 'red car at night', 'Orvis receipt', 'dashboard with orange needles'])
    assert.equal(looksLikeKeyword(d), false, d)
})

test('what each mode sends', () => {
  const cleared = { q: '', semantic: '', text: '', color: null, similar: null, image: null }
  assert.deepEqual(buildSearch('brown boots', 'auto'), { ...cleared, q: 'brown boots', semantic: 'brown boots' })
  assert.deepEqual(buildSearch('IMG_2231', 'auto'), { ...cleared, q: 'IMG_2231' })
  // quotes ask for the words; they're not part of them
  assert.deepEqual(buildSearch('"zero trust"', 'auto'), { ...cleared, q: 'zero trust' })
  assert.deepEqual(buildSearch('#2F4F3A', 'auto'), { ...cleared, color: '#2f4f3a' })
  assert.deepEqual(buildSearch('#abc', 'auto'), { ...cleared, color: '#aabbcc' })
  assert.deepEqual(buildSearch('brown boots', 'words'), { ...cleared, q: 'brown boots' })
  assert.deepEqual(buildSearch('brown boots', 'meaning'), { ...cleared, semantic: 'brown boots' })
  assert.deepEqual(buildSearch('zero trust', 'text'), { ...cleared, text: 'zero trust' })
  assert.deepEqual(buildSearch('2f4f3a', 'color'), { ...cleared, color: '#2f4f3a' })
  // emptying the box clears every box search (the view and filters stay)
  assert.deepEqual(buildSearch('   ', 'meaning'), cleared)
})

test('hexColor: only real hex colours; # needed unless asked', () => {
  assert.equal(hexColor('#fff'), '#ffffff')
  assert.equal(hexColor('fff'), null)
  assert.equal(hexColor('fff', false), '#ffffff')
  assert.equal(hexColor('#ggg'), null)
  assert.equal(hexColor('#12345'), null)
})

test('the box shows the search and reads back its mode', () => {
  assert.equal(searchText(q({ q: 'a', semantic: 'a' })), 'a')
  assert.equal(searchText(q({ semantic: 'b' })), 'b')
  assert.equal(searchText(q({ text: 'c' })), 'c')
  assert.equal(modeOf(q({ q: 'dog', semantic: 'dog' })), 'auto')
  assert.equal(modeOf(q({ semantic: 'dog' })), 'meaning')
  assert.equal(modeOf(q({ text: 'dog' })), 'text')
  assert.equal(modeOf(q({ image: 'img1' })), 'image')
  // a plain word search is Auto's when Auto would have sent it, else Words
  assert.equal(modeOf(q({ q: 'IMG_1' }), 'auto'), 'auto')
  assert.equal(modeOf(q({ q: 'dog' }), 'auto'), 'words')
  assert.equal(modeOf(q({ q: 'dog' }), 'words'), 'words')
  assert.equal(modeOf(q({}), 'meaning'), 'meaning')
})

test('ranked searches and item-started searches', () => {
  assert.equal(isRanked(q({ q: 'x' })), false)
  for (const p of [{ semantic: 'x' }, { similar: 'I' }, { color: '#000000' }, { text: 'x' }, { image: 'i' }])
    assert.equal(isRanked(q(p)), true)
  const cur = q({ folder: 'F1', q: 'old', order: 'filename' })
  // more like this and a colour look across the whole library
  assert.deepEqual(moreLikeThis('I9', cur), q({ similar: 'I9', order: 'filename' }))
  assert.deepEqual(colourSearch('#ABCDEF', cur), q({ color: '#abcdef', color_tol: 30, order: 'filename' }))
  assert.equal(searchTitle(q({ similar: 'I9' }), { item: () => 'boots.jpg' }), 'More like “boots.jpg”')
  assert.equal(searchTitle(q({ color: '#abcdef' })), 'Colour #abcdef')
  assert.equal(searchTitle(q({ q: 'x' })), null)
})

test('match labels say which ways matched', () => {
  assert.equal(matchLabel('hybrid', q({ q: 'a', semantic: 'a' })), 'Words + meaning')
  assert.equal(matchLabel('hybrid', q({ q: 'a', image: 'i' })), 'Words + look')
  assert.equal(matchLabel('hybrid', q({ q: 'a', similar: 'I' })), 'Words + look')
  assert.equal(matchLabel('text'), 'Text in image')
})

test('snippets: << >> marks become highlighted parts', () => {
  assert.deepEqual(parseSnippet('the <<zero trust>> slide'), [
    { text: 'the ', hit: false },
    { text: 'zero trust', hit: true },
    { text: ' slide', hit: false },
  ])
  assert.deepEqual(parseSnippet('<<a>><<b>> c'), [
    { text: 'ab', hit: true },
    { text: ' c', hit: false },
  ])
  // an unclosed mark runs to the end; a stray >> is text; empty marks vanish
  assert.deepEqual(parseSnippet('x <<y'), [
    { text: 'x ', hit: false },
    { text: 'y', hit: true },
  ])
  assert.deepEqual(parseSnippet('a >> b <<>> c'), [{ text: 'a >> b  c', hit: false }])
  assert.deepEqual(parseSnippet(null), [])
  assert.equal(snippetText('…ORVIS <<receipt>> #4821…'), '…ORVIS receipt #4821…')
})

test('the new searches narrow the view and are saved by smart folders', () => {
  for (const p of [{ semantic: 'x' }, { similar: 'I' }, { color: '#000000' }, { text: 'x' }, { person: 'P' }, { image: 'i' }])
    assert.equal(hasNarrowing(q(p)), true)
  const saved = smartFolderQuery(q({ semantic: 'dog', color: '#000000', color_tol: 20, text: 't', person: 'P', similar: 'I', image: 'i' }))
  assert.equal(saved.semantic, 'dog')
  assert.equal(saved.color_tol, 20)
  assert.equal(saved.person, 'P')
  // not an item or an image kept in this tab
  assert.equal('similar' in saved, false)
  assert.equal('image' in saved, false)
  // what the server hands back for a smart folder saved before these fields
  const back = normalizeSaved({ q: '', tags: ['Rolex'], semantic: null, text: null, color: null, color_tol: null } as unknown as Partial<Query>)
  assert.equal(back.semantic, '')
  assert.equal(back.text, '')
  assert.equal(back.color_tol, 30)
  const e = emptyState(q({ semantic: 'red car' }), { libraryEmpty: false, mobile: false, title: 'All items' })
  assert.equal(e.text, 'Nothing matches “red car”.')
})

test('images to search by are kept for the tab, newest few', () => {
  const blob = new Blob(['x'], { type: 'image/png' })
  const id = registerImage(blob, 1)
  assert.equal(imageFor(id), blob)
  for (let i = 0; i < 10; i++) registerImage(new Blob(['y']), 2)
  assert.equal(imageFor(id), undefined)
  const files = [{ type: 'text/plain' }, { type: 'image/jpeg', name: 'a' }] as unknown as File[]
  assert.equal(firstImage(files)?.type, 'image/jpeg')
  assert.equal(firstImage(null), null)
})

test('badges only where results matched in different ways', () => {
  // words + meaning: the ones the words found say so
  assert.equal(showMatchBadge('hybrid', q({ q: 'a', semantic: 'a' })), true)
  assert.equal(showMatchBadge('keyword', q({ q: 'a', semantic: 'a' })), true)
  assert.equal(showMatchBadge('meaning', q({ q: 'a', semantic: 'a' })), false)
  // one way only: no badge on every tile
  assert.equal(showMatchBadge('meaning', q({ semantic: 'a' })), false)
  assert.equal(showMatchBadge('keyword', q({ q: 'a' })), false)
  assert.equal(showMatchBadge('similar', q({ similar: 'I' })), false)
  assert.equal(showMatchBadge(undefined, q({ q: 'a', semantic: 'a' })), false)
})

test('a ranked search that can’t run says why', () => {
  assert.match(rankedErrorText(503, q({ semantic: 'dog' })) ?? '', /isn’t available/)
  assert.equal(rankedErrorText(503, q({ q: 'dog' })), null)
  assert.match(rankedErrorText(410, q({ image: 'x' })) ?? '', /drop or paste/i)
  assert.equal(rankedErrorText(500, q({ semantic: 'dog' })), null)
})

test('#tag words typed in the box are tags, not text', () => {
  assert.deepEqual(tagTokens('#watch'), { tags: ['watch'], rest: '' })
  assert.deepEqual(tagTokens('#brand:* red dial #watch #watch'), { tags: ['brand:*', 'watch'], rest: 'red dial' })
  assert.deepEqual(tagTokens('#2f4f3a'), { tags: [], rest: '#2f4f3a' }) // a colour
  assert.deepEqual(tagTokens('dog on grass'), { tags: [], rest: 'dog on grass' })
  assert.deepEqual(tagTokens('C# #'), { tags: [], rest: 'C# #' })
})
