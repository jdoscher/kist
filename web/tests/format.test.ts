// Display formatting. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fmtBytes, fmtDuration, linkableUrl } from '../src/format.ts'

test('fmtBytes: one rule everywhere (1 decimal under 10, none from 10)', () => {
  assert.equal(fmtBytes(0), '0 B')
  assert.equal(fmtBytes(1023), '1023 B')
  assert.equal(fmtBytes(1024), '1.0 KB')
  assert.equal(fmtBytes(1536), '1.5 KB')
  assert.equal(fmtBytes(340 * 1024), '340 KB')
  assert.equal(fmtBytes(2.5 * 1024 ** 2), '2.5 MB')
  assert.equal(fmtBytes(1.23 * 1024 ** 3), '1.2 GB') // sidebar and panel now agree
  assert.equal(fmtBytes(12.4 * 1024 ** 3), '12 GB')
  assert.equal(fmtBytes(9.97 * 1024 ** 2), '10 MB') // no "10.0"
  assert.equal(fmtBytes(1023.9 * 1024), '1.0 MB') // no "1024 KB"
  assert.equal(fmtBytes(3 * 1024 ** 4), '3.0 TB')
  assert.equal(fmtBytes(NaN), '0 B')
})

test('fmtDuration: m:ss, h:mm:ss, never 1:60', () => {
  assert.equal(fmtDuration(0), '0:00')
  assert.equal(fmtDuration(5.2), '0:05')
  assert.equal(fmtDuration(59.6), '1:00')
  assert.equal(fmtDuration(65), '1:05')
  assert.equal(fmtDuration(3599.5), '1:00:00')
  assert.equal(fmtDuration(3725), '1:02:05')
  assert.equal(fmtDuration(-3), '0:00')
})

test('linkableUrl: only absolute http(s), never throws', () => {
  assert.deepEqual(linkableUrl('https://example.com/a?b=1'), {
    href: 'https://example.com/a?b=1',
    host: 'example.com',
  })
  assert.equal(linkableUrl('not a url'), null)
  assert.equal(linkableUrl('javascript:alert(1)'), null)
  assert.equal(linkableUrl('/relative/path'), null)
  assert.equal(linkableUrl(''), null)
  assert.equal(linkableUrl(null), null)
})
