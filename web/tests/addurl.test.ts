// Add from URL (click-test 2.0): the ⌘K action opens a box for a link,
// not the file picker, and the box turns typed text into URL jobs.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { jobsFromText } from '../src/clipboard.ts'
import { addActionOpens } from '../src/palette.ts'

test('Add from URL asks for a link; Add files opens the picker', () => {
  assert.equal(addActionOpens('add-url'), 'url')
  assert.equal(addActionOpens('upload'), 'files')
  assert.equal(addActionOpens('new-folder'), null)
})

test('the URL box takes image and page URLs, once each, and nothing else', () => {
  const img = 'https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg'
  const page = 'https://en.wikipedia.org/wiki/Barcelona_chair'
  assert.deepEqual(jobsFromText(`${img}\n${page} ${img}`), [
    { kind: 'url', url: img },
    { kind: 'url', url: page },
  ])
  assert.deepEqual(jobsFromText('not a url'), [])
  assert.deepEqual(jobsFromText('javascript:alert(1)'), [])
})
