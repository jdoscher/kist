// Token presets, scope labels and what a token may do in the web app
// (src/tokens.ts), plus the bulk batch id. Run with `npm test`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { newBatchId } from '../src/api.ts'
import { fmtAgeHours, percent } from '../src/format.ts'
import { cantBrowseText, capsOf, helperCommand, PRESETS, scopeText, tokenRequest, tokenStatus } from '../src/tokens.ts'

test('each preset maps to the server scopes', () => {
  const map = Object.fromEntries(PRESETS.map((p) => [p.id, p.scopes]))
  assert.deepEqual(map, {
    family: ['read'],
    share: ['add'],
    agent: ['agent'],
    helper: ['helper'],
    full: ['admin'],
  })
  for (const p of PRESETS) assert.ok(p.explain.length > 40, `${p.id} explains itself`)
})

test('the create request carries folders only for read-only tokens', () => {
  assert.deepEqual(tokenRequest(' Patty ', 'family', ['F1']), {
    name: 'Patty',
    scopes: ['read'],
    folder_ids: ['F1'],
    expires_at: null,
  })
  assert.deepEqual(tokenRequest('Claude', 'agent', ['F1']).folder_ids, [])
  const exp = tokenRequest('x', 'share', [], '2027-01-31').expires_at
  assert.ok(exp && new Date(exp).getDate() === 31, 'expires at the end of the chosen day')
})

test('scopes read as the preset they match', () => {
  assert.equal(scopeText(['read'], ['F1']), 'Family read-only')
  assert.equal(scopeText(['read'], []), 'Read-only')
  assert.equal(scopeText(['agent']), 'Agent')
  assert.equal(scopeText(['admin']), 'Full access')
  assert.equal(scopeText(['write']), 'Edit everything')
  assert.equal(scopeText(['add', 'read']), 'Add only, Read-only')
})

test('token status: revoked, expired, active', () => {
  const now = Date.parse('2026-11-09T12:00:00Z')
  assert.equal(tokenStatus({ revoked_at: '2026-11-01T00:00:00Z', expires_at: null }, now), 'revoked')
  assert.equal(tokenStatus({ revoked_at: null, expires_at: '2026-11-08T00:00:00Z' }, now), 'expired')
  assert.equal(tokenStatus({ revoked_at: null, expires_at: '2027-01-01T00:00:00Z' }, now), 'active')
})

test('what each token may do in the web app', () => {
  assert.deepEqual(capsOf({ scopes: ['admin'], folder_ids: [] }), {
    canBrowse: true,
    canEdit: true,
    canDestroy: true,
    isAdmin: true,
    restricted: false,
  })
  const agent = capsOf({ scopes: ['agent'], folder_ids: [] })
  assert.ok(agent.canEdit && !agent.canDestroy && !agent.isAdmin)
  const family = capsOf({ scopes: ['read'], folder_ids: ['F1'] })
  assert.ok(family.canBrowse && !family.canEdit && family.restricted)
  assert.ok(!capsOf({ scopes: ['read'], folder_ids: [] }).restricted)
  assert.ok(capsOf(undefined).isAdmin, 'an older server without /api/me is the owner')
  assert.match(cantBrowseText({ scopes: ['add'] }) ?? '', /only add/)
  assert.match(cantBrowseText({ scopes: ['helper'] }) ?? '', /helper/)
  assert.equal(cantBrowseText({ scopes: ['read'] }), null)
  assert.equal(helperCommand('https://kist.example', 'kist_x'), 'kist helper --url https://kist.example --token kist_x')
})

test('one bulk action is one batch id the server accepts', () => {
  const id = newBatchId(Date.parse('2026-11-09T12:00:00Z'), () => 0.5)
  assert.match(id, /^[A-Za-z0-9._:-]{1,64}$/)
  assert.notEqual(newBatchId(), newBatchId())
})

test('ages and percentages', () => {
  assert.equal(fmtAgeHours(null), 'never')
  assert.equal(fmtAgeHours(0.4), 'under an hour')
  assert.equal(fmtAgeHours(1), '1 hour')
  assert.equal(fmtAgeHours(30), '30 hours')
  assert.equal(fmtAgeHours(100), '4 days')
  assert.equal(percent(5, 0), 100)
  assert.equal(percent(1, 3), 33)
})
