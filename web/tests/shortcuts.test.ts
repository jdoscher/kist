// Global keyboard shortcuts: one owner, overlays block what's behind them.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createShortcuts, isTyping, type KeyLike } from '../src/shortcuts.ts'

const field = { closest: (sel: string) => (sel.includes('input') ? {} : null) }
const body = { closest: () => null }

function key(k: string, target: unknown = body): KeyLike & { prevented: boolean } {
  return {
    key: k,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    target: target as EventTarget,
    prevented: false,
    preventDefault() {
      this.prevented = true
    },
  }
}

function setup() {
  const s = createShortcuts()
  const log: string[] = []
  // what Grid/BulkBar and Preview register
  s.on('grid', (e) => {
    if (e.key === 'Delete' || e.key === 'Backspace') {
      log.push('trash')
      return true
    }
  })
  s.on('preview', (e) => {
    if (e.key === 'ArrowRight') {
      log.push('next')
      return true
    }
    if (e.key === 'Escape') {
      log.push('close-preview')
      return true
    }
  })
  return { s, log }
}

test('Delete trashes from the grid', () => {
  const { s, log } = setup()
  assert.equal(s.topLayer(), 'grid')
  assert.equal(s.dispatch(key('Delete')), true)
  assert.deepEqual(log, ['trash'])
})

test('Delete/Backspace do nothing while the preview is open', () => {
  const { s, log } = setup()
  const close = s.openLayer('preview')
  s.dispatch(key('Delete'))
  s.dispatch(key('Backspace'))
  s.dispatch(key('ArrowRight'))
  assert.deepEqual(log, ['next'])
  close()
  s.dispatch(key('Delete'))
  assert.deepEqual(log, ['next', 'trash'])
})

test('a dialog or the Needs attention panel blocks grid and preview keys', () => {
  const { s, log } = setup()
  const closePreview = s.openLayer('preview')
  const closeModal = s.openLayer('modal')
  assert.equal(s.topLayer(), 'modal')
  s.dispatch(key('Delete'))
  s.dispatch(key('Escape'))
  assert.deepEqual(log, [])
  closeModal()
  closeModal() // closing twice is harmless
  assert.equal(s.topLayer(), 'preview')
  closePreview()
  assert.equal(s.topLayer(), 'grid')
})

test('keys typed into a field never reach a shortcut', () => {
  const { s, log } = setup()
  assert.equal(isTyping(field as unknown as EventTarget), true)
  assert.equal(isTyping(null), false)
  assert.equal(s.dispatch(key('Backspace', field)), false)
  assert.deepEqual(log, [])
})

test('newest handler on a layer wins; unregistering removes it', () => {
  const s = createShortcuts()
  const log: string[] = []
  s.on('grid', () => void log.push('old'))
  const off = s.on('grid', () => {
    log.push('new')
    return true
  })
  s.dispatch(key('x'))
  off()
  s.dispatch(key('x'))
  assert.deepEqual(log, ['new', 'old'])
})
