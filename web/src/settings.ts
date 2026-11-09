// Persisted view preferences (no React; tests/settings.test.ts). Every
// pref goes through readPrefs/savePref so private mode and bad stored
// values are handled in one place.

import { TILE_SIZE_DEFAULT, TILE_SIZE_MAX, TILE_SIZE_MIN, type GridLayout } from './layout.ts'
import type { MobileCols } from './mobile.ts'

export interface Prefs {
  panelOpen: boolean
  gridLayout: GridLayout
  tileSize: number
  mobileCols: MobileCols
}

const KEYS: Record<keyof Prefs, string> = {
  panelOpen: 'kist.panelOpen',
  gridLayout: 'kist.gridLayout',
  tileSize: 'kist.tileSize',
  mobileCols: 'kist.mobileCols',
}

type Store = Pick<Storage, 'getItem' | 'setItem'>

function browserStore(): Store | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null // private mode / blocked storage
  }
}

export function clampSize(n: number): number {
  if (!Number.isFinite(n)) return TILE_SIZE_DEFAULT
  return Math.min(TILE_SIZE_MAX, Math.max(TILE_SIZE_MIN, Math.round(n)))
}

/** The stored prefs, with defaults for anything missing or unreadable. */
export function readPrefs(store: Store | null = browserStore()): Prefs {
  const get = (k: keyof Prefs): string | null => {
    try {
      return store?.getItem(KEYS[k]) ?? null
    } catch {
      return null
    }
  }
  const size = get('tileSize')
  return {
    panelOpen: get('panelOpen') !== '0',
    gridLayout: get('gridLayout') === 'square' ? 'square' : 'justified',
    tileSize: size === null ? TILE_SIZE_DEFAULT : clampSize(Number(size)),
    mobileCols: get('mobileCols') === '2' ? 2 : 3,
  }
}

function encode<K extends keyof Prefs>(key: K, value: Prefs[K]): string {
  if (key === 'panelOpen') return value ? '1' : '0'
  return String(value)
}

export function savePref<K extends keyof Prefs>(key: K, value: Prefs[K], store: Store | null = browserStore()) {
  try {
    store?.setItem(KEYS[key], encode(key, value))
  } catch {
    /* private mode */
  }
}
