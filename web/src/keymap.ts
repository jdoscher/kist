// Every keyboard shortcut in the web app, one table per screen (no React;
// tests/keymap.test.ts). The handlers live next to what they drive
// (Grid.tsx, Creative.tsx, Preview.tsx, Annotations.tsx, QuickLook.tsx,
// Triage.tsx, Compare.tsx, board/keys.ts); this is the list they must agree
// with. The test checks that no key does two things on one screen, that the
// handlers' letter keys are all listed here, and that the README's keyboard
// section is this table (keymapMarkdown()).
//
// Keys are written `Mod+K` (⌘ on a Mac, Ctrl elsewhere), `Shift+Mod+G`,
// `ArrowLeft`, `Space`. A screen is a shortcuts.ts layer as the user sees it:
// the library and a board are both the 'grid' layer but never shown at once.

export type KeyContext = 'library' | 'quicklook' | 'preview' | 'triage' | 'compare' | 'board'

export interface Binding {
  keys: string[]
  does: string
}

export const CONTEXT_NAMES: Record<KeyContext, string> = {
  library: 'Library grid',
  quicklook: 'Quick look (holding Space over a tile)',
  preview: 'Full-screen preview',
  triage: 'Triage (/triage)',
  compare: 'Compare and survey',
  board: 'Board canvas (/board/…)',
}

export const KEYMAP: Record<KeyContext, Binding[]> = {
  library: [
    { keys: ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'], does: 'move and select' },
    { keys: ['Shift+ArrowLeft', 'Shift+ArrowRight', 'Shift+ArrowUp', 'Shift+ArrowDown'], does: 'extend the selection' },
    { keys: ['Mod+ArrowLeft', 'Mod+ArrowRight', 'Mod+ArrowUp', 'Mod+ArrowDown'], does: 'move without selecting' },
    { keys: ['Home', 'End'], does: 'first, last item' },
    { keys: ['Space'], does: 'tap: add or remove the item; hold: quick look' },
    { keys: ['Enter'], does: 'open full screen' },
    { keys: ['Escape'], does: 'clear the selection' },
    { keys: ['Mod+A'], does: 'select all' },
    { keys: ['Delete', 'Backspace'], does: 'move the selection to the Trash (in the Trash: delete forever)' },
    { keys: ['S'], does: 'open or close the focused stack' },
    { keys: ['C'], does: 'compare the selection (2–4 items)' },
    { keys: ['N'], does: 'survey the selection: pick and reject one by one' },
    { keys: ['ContextMenu'], does: 'the item menu (More like this, colour, stack)' },
    { keys: ['Mod+Z'], does: 'Recent changes, focused on the newest Undo' },
    { keys: ['Mod+K'], does: 'command palette' },
  ],
  quicklook: [
    { keys: ['Space'], does: 'release: close (once latched, press again)' },
    { keys: ['Z'], does: 'loupe (100%) on or off; latches the peek open' },
    { keys: ['Escape'], does: 'close' },
  ],
  preview: [
    { keys: ['ArrowRight', 'ArrowDown'], does: 'next item' },
    { keys: ['ArrowLeft', 'ArrowUp'], does: 'previous item' },
    { keys: ['Escape'], does: 'cancel a note, tool or loupe; else close' },
    { keys: ['A'], does: 'show or hide notes (annotations)' },
    { keys: ['Z'], does: 'loupe on or off (images)' },
    { keys: ['M'], does: 'note at the current time (video)' },
    { keys: ['I'], does: 'mark in (video)' },
    { keys: ['O'], does: 'mark out: a note over the range (video)' },
    { keys: ['Mod+K'], does: 'command palette' },
  ],
  triage: [
    { keys: ['P'], does: 'pick' },
    { keys: ['X'], does: 'reject' },
    { keys: ['U'], does: 'unflag' },
    { keys: ['0', '1', '2', '3', '4', '5'], does: 'rating (0 clears)' },
    { keys: ['6', '7', '8', '9'], does: 'red, orange, yellow, green label (again: clear)' },
    { keys: ['T'], does: 'tag' },
    { keys: ['F'], does: 'file in a folder' },
    { keys: ['ArrowRight', 'J'], does: 'next' },
    { keys: ['ArrowLeft', 'K'], does: 'previous' },
    { keys: ['Mod+Z'], does: 'undo the last change' },
    { keys: ['Delete', 'Backspace'], does: 'move to the Trash' },
    { keys: ['Escape'], does: 'leave triage' },
    { keys: ['Mod+K'], does: 'command palette' },
  ],
  compare: [
    { keys: ['P'], does: 'pick the current item' },
    { keys: ['X'], does: 'reject it' },
    { keys: ['U'], does: 'unflag it' },
    { keys: ['1', '2', '3', '4', '5'], does: 'rating (survey)' },
    { keys: ['6', '7', '8', '9'], does: 'red, orange, yellow, green label' },
    { keys: ['ArrowRight', 'J'], does: 'next item' },
    { keys: ['ArrowLeft', 'K'], does: 'previous item' },
    { keys: ['+', '='], does: 'zoom in (all panes)' },
    { keys: ['-'], does: 'zoom out' },
    { keys: ['0'], does: 'fit' },
    { keys: ['Escape'], does: 'close' },
    { keys: ['Mod+K'], does: 'command palette' },
  ],
  board: [
    { keys: ['V'], does: 'select tool' },
    { keys: ['H'], does: 'hand (pan) tool' },
    { keys: ['C'], does: 'connect (arrow) tool' },
    { keys: ['N'], does: 'add a note' },
    { keys: ['T'], does: 'add text' },
    { keys: ['R'], does: 'add a shape' },
    { keys: ['Space (hold)'], does: 'pan while held' },
    { keys: ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'], does: 'nudge 1px' },
    { keys: ['Shift+ArrowLeft', 'Shift+ArrowRight', 'Shift+ArrowUp', 'Shift+ArrowDown'], does: 'nudge a grid step' },
    { keys: ['Tab', 'Shift+Tab'], does: 'next, previous thing on the canvas' },
    { keys: ['Delete', 'Backspace'], does: 'delete the selection or arrow' },
    { keys: ['Mod+D'], does: 'duplicate' },
    { keys: ['Mod+G'], does: 'group' },
    { keys: ['Shift+Mod+G'], does: 'ungroup' },
    { keys: ['Mod+A'], does: 'select all' },
    { keys: ['Mod+Z'], does: 'undo' },
    { keys: ['Shift+Mod+Z', 'Mod+Y'], does: 'redo' },
    { keys: ['+', '='], does: 'zoom in' },
    { keys: ['-'], does: 'zoom out' },
    { keys: ['0'], does: 'zoom to fit' },
    { keys: ['1'], does: 'zoom to 100%' },
    { keys: ['Escape'], does: 'clear the selection, back to the select tool' },
    { keys: ['Mod+K'], does: 'command palette' },
  ],
}

/** One spelling per key: modifiers in a fixed order, letters upper case. */
export function normalizeKey(k: string): string {
  // '+' alone, or ending a chord ('Shift++'), is the plus key
  const plus = k === '+' || k.endsWith('++')
  const parts = plus ? k.slice(0, -1).split('+').filter(Boolean) : k.split('+')
  const key = plus ? '+' : (parts.pop() as string)
  const mods = new Set(parts)
  const order = ['Shift', 'Alt', 'Mod'].filter((m) => mods.has(m))
  return [...order, key.length === 1 ? key.toUpperCase() : key].join('+')
}

/** Keys bound more than once on a screen (should be none). */
export function duplicateBindings(map: Record<string, Binding[]> = KEYMAP): string[] {
  const out: string[] = []
  for (const [ctx, list] of Object.entries(map)) {
    const seen = new Map<string, string>()
    for (const b of list)
      for (const k of b.keys) {
        const n = normalizeKey(k)
        if (seen.has(n)) out.push(`${ctx}: ${n} (“${seen.get(n)}” and “${b.does}”)`)
        else seen.set(n, b.does)
      }
  }
  return out
}

const SHOW: Record<string, string> = {
  ArrowLeft: '←',
  ArrowRight: '→',
  ArrowUp: '↑',
  ArrowDown: '↓',
  Mod: '⌘/Ctrl',
  Shift: '⇧',
}

function showKey(k: string): string {
  if (k === '+') return '+'
  return k
    .split('+')
    .map((p) => SHOW[p] ?? p)
    .join(' ')
}

/** The README's keyboard section (tests/keymap.test.ts keeps them equal). */
export function keymapMarkdown(map: Record<KeyContext, Binding[]> = KEYMAP): string {
  const lines: string[] = []
  for (const ctx of Object.keys(CONTEXT_NAMES) as KeyContext[]) {
    lines.push(`**${CONTEXT_NAMES[ctx]}**`, '', '| Keys | Does |', '| --- | --- |')
    for (const b of map[ctx]) lines.push(`| ${b.keys.map((k) => `\`${showKey(k)}\``).join(' ')} | ${b.does} |`)
    lines.push('')
  }
  return lines.join('\n').trimEnd() + '\n'
}
