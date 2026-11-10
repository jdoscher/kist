// Board keyboard: arrows nudge (Shift ×10, or a grid step), Delete
// removes, ⌘D duplicates, ⌘G groups (⇧⌘G ungroups), ⌘A selects all,
// ⌘Z / ⇧⌘Z undo and redo, Escape clears, V/H/C pick a tool, N/T/R add a
// note, text or shape, +/−/0/1 zoom, Space held pans.

import { useEffect, useRef } from 'react'
import { boardTakesKey, duplicate, group, moveNodes, removeNodes, ungroup, type BoardKeyPlace } from '../../../board'
import { isTyping } from '../../../shortcuts'
import { useShortcut } from '../../../useShortcuts'
import type { Editor } from './BoardEditor'

export function useBoardKeys(ed: Editor | null, active: boolean) {
  const space = useRef(false)

  useEffect(() => {
    if (!ed || !active) return
    const down = (e: KeyboardEvent) => {
      if (e.key === ' ' && !(e.target as HTMLElement).closest?.('input, textarea, select, button, [contenteditable]')) {
        if (!space.current) {
          space.current = true
          document.body.classList.add('board-space')
        }
        e.preventDefault()
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.key === ' ') {
        space.current = false
        document.body.classList.remove('board-space')
      }
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      document.body.classList.remove('board-space')
    }
  }, [ed === null, active]) // eslint-disable-line react-hooks/exhaustive-deps

  useShortcut(
    'grid',
    (e) => {
      if (!ed) return false
      const t = e.target as HTMLElement | null
      const c = ed.content
      const mod = e.metaKey || e.ctrlKey
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key
      // keys belong to the board when focus is on it (or nowhere); its ⌘
      // shortcuts also when focus is on its toolbar
      if (!boardTakesKey(keyPlace(t), mod, k)) return false
      const sel = ed.sel
      const done = () => {
        e.preventDefault()
        return true
      }
      const zoomBy = (f: number) => {
        const { w, h } = ed.viewSize()
        const zoom = Math.min(8, Math.max(0.05, ed.vp.zoom * f))
        ed.setVp({ zoom, x: w / 2 - ((w / 2 - ed.vp.x) / ed.vp.zoom) * zoom, y: h / 2 - ((h / 2 - ed.vp.y) / ed.vp.zoom) * zoom })
      }
      // view keys work read-only too
      if (!mod && (k === '+' || k === '=')) return zoomBy(1.25), done()
      if (!mod && k === '-') return zoomBy(1 / 1.25), done()
      if (!mod && k === '0') return ed.fit(), done()
      if (!mod && k === '1') return zoomBy(1 / ed.vp.zoom), done()
      if (k === 'Escape') {
        if (!sel.length && !ed.edge && ed.tool === 'select') return false
        ed.setSel([])
        ed.setEdge(null)
        ed.setTool('select')
        return done()
      }
      if (!ed.canEdit) return false
      if (mod && k === 'z') return (e.shiftKey ? ed.redo() : ed.undo()), done()
      if (mod && k === 'y') return ed.redo(), done()
      if (mod && k === 'a') return ed.setSel(c.nodes.map((n) => n.id)), done()
      if (!mod && !e.altKey) {
        if (k === 'v') return ed.setTool('select'), done()
        if (k === 'h') return ed.setTool('hand'), done()
        if (k === 'c') return ed.setTool('connect'), done()
        if (k === 'n') return ed.add('note'), done()
        if (k === 't') return ed.add('text'), done()
        if (k === 'r') return ed.add('shape'), done()
      }
      if ((k === 'Delete' || k === 'Backspace') && (sel.length || ed.edge)) {
        if (ed.edge) {
          ed.commit({ ...c, edges: c.edges.filter((x) => x.id !== ed.edge) })
          ed.setEdge(null)
          ed.announce('Arrow deleted')
        } else {
          ed.commit({ ...c, ...removeNodes(c, sel) })
          ed.announce(`Deleted ${sel.length}`)
          ed.setSel([])
        }
        return done()
      }
      if (!sel.length) return false
      if (mod && k === 'd') {
        const out = duplicate(c, sel, ed.grid || 24)
        ed.commit({ ...c, nodes: out.nodes, edges: out.edges })
        ed.setSel(out.newIds)
        ed.announce(`Duplicated ${sel.length}`)
        return done()
      }
      if (mod && k === 'g') {
        if (e.shiftKey) {
          ed.commit({ ...c, nodes: ungroup(c.nodes, sel) })
          ed.setSel([])
          ed.announce('Ungrouped')
        } else {
          const g = group(c.nodes, sel)
          if (g) {
            ed.commit({ ...c, nodes: g.nodes })
            ed.setSel([g.id])
            ed.announce('Grouped')
          }
        }
        return done()
      }
      const arrows: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
      if (arrows[k] && !mod) {
        const step = e.shiftKey ? (ed.grid || 10) : 1
        const [dx, dy] = arrows[k]
        ed.commit({ ...c, nodes: moveNodes(c.nodes, sel, dx * step, dy * step) })
        return done()
      }
      return false
    },
    !!ed,
  )
}

function keyPlace(t: HTMLElement | null): BoardKeyPlace {
  if (!t || t === document.body) return 'board'
  if (isTyping(t)) return 'text'
  if (t.closest?.('.board-viewport, .board-list, .board-inspector')) return 'board'
  return t.closest?.('.board-screen') ? 'chrome' : 'outside'
}
