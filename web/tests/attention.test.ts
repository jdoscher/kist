import { test } from 'node:test'
import assert from 'node:assert/strict'
import { attentionCount } from '../src/attention.ts'

test('a gone source page never counts as needing attention', () => {
  const stats = { failed_jobs: 1, preview_failed: 2, fixity_failed: 3, source_gone: 5 }
  assert.equal(attentionCount(stats), 6)
  assert.equal(attentionCount({ failed_jobs: 0, preview_failed: 0, fixity_failed: 0, source_gone: 5 } as never), 0)
  assert.equal(attentionCount(null), 0)
})
