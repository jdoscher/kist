// The boards list: the whole card opens a board. Run with `npm test`.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const tsx = readFileSync(new URL('../src/components/m3/BoardsScreen.tsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/creative.css', import.meta.url), 'utf8')

test('the whole card opens the board, except its other buttons (blank space in the actions row opens it too)', () => {
  const li = tsx.slice(tsx.indexOf('<li'), tsx.indexOf('<button className="board-open"'))
  assert.match(li, /onClick=/)
  assert.match(li, /closest\('button, a, input'\)/)
  assert.match(li, /!control\.classList\.contains\('board-open'\)/)
  assert.match(li, /openScreen\(\{ kind: 'board', id: b\.id \}\)/)
  assert.match(css, /\.board-card\.opens \{[^}]*cursor: pointer/)
  // a stretched ::after breaks under the buttons' :active scale (see creative.css)
  assert.doesNotMatch(css, /\.board-open::after/)
})
