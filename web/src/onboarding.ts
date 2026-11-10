// The first-run "Set up Kist" checklist (Milestone 3, brand U5), no React
// (tests/onboarding.test.ts). Each step ticks itself off from real state
// where Kist can see it; the rest can be marked done by hand. Dismissing
// hides it for good (on this browser).

export type StepId = 'items' | 'extension' | 'agent' | 'phone' | 'backup'

export interface Step {
  id: StepId
  title: string
  detail: string
  done: boolean
  /** ticked from real state (not by hand) */
  auto: boolean
}

export interface SetupFacts {
  totalItems?: number
  /** token names and batch actors/sources seen (lower-cased) */
  actors?: string[]
  sources?: string[]
  standalone?: boolean
  backupOk?: boolean
  /** steps marked done by hand */
  manual?: Partial<Record<StepId, boolean>>
}

const EXT = /firefox|chrome|safari|extension|browser/
const AGENT = /claude|mcp|agent|cursor|gpt|llm/

export const ITEMS_GOAL = 10

export function setupSteps(f: SetupFacts): Step[] {
  const actors = (f.actors ?? []).map((a) => a.toLowerCase())
  const sources = (f.sources ?? []).map((s) => s.toLowerCase())
  const manual = f.manual ?? {}
  const auto = {
    items: (f.totalItems ?? 0) >= ITEMS_GOAL,
    extension: actors.some((a) => EXT.test(a)),
    agent: sources.includes('mcp') || actors.some((a) => AGENT.test(a)),
    phone: !!f.standalone,
    backup: !!f.backupOk,
  }
  const step = (id: StepId, title: string, detail: string): Step => ({
    id,
    title,
    detail,
    done: auto[id] || !!manual[id],
    auto: auto[id],
  })
  return [
    step('items', `Add ${ITEMS_GOAL} items`, `${Math.min(f.totalItems ?? 0, ITEMS_GOAL)} of ${ITEMS_GOAL}: drop, paste or upload anything.`),
    step('extension', 'Save from your browser', 'Install the Kist extension for Firefox or Chrome and give it a token.'),
    step('agent', 'Connect an AI agent', 'Add Kist to Claude (or any MCP client) so it can search, tag and file.'),
    step('phone', 'Put Kist on your phone', 'Open Kist in Safari, then Share → Add to Home Screen.'),
    step('backup', 'Check your backups', 'Settings → Archive health shows the last backup and verify run.'),
  ]
}

export function progress(steps: Step[]): { done: number; total: number } {
  return { done: steps.filter((s) => s.done).length, total: steps.length }
}

const KEY = 'kist.setup'

type Store = Pick<Storage, 'getItem' | 'setItem'>

function store(): Store | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export interface SetupPrefs {
  dismissed: boolean
  manual: Partial<Record<StepId, boolean>>
  /** this browser has run Kist from the home screen */
  standalone: boolean
}

export function readSetup(s: Store | null = store()): SetupPrefs {
  try {
    const v = JSON.parse(s?.getItem(KEY) ?? '{}')
    return { dismissed: !!v.dismissed, manual: v.manual && typeof v.manual === 'object' ? v.manual : {}, standalone: !!v.standalone }
  } catch {
    return { dismissed: false, manual: {}, standalone: false }
  }
}

export function saveSetup(p: SetupPrefs, s: Store | null = store()) {
  try {
    s?.setItem(KEY, JSON.stringify(p))
  } catch {
    /* private mode */
  }
}

/** The MCP command for this server, ready to paste (the token stays out:
 * you put your own in). */
export function mcpCommand(origin: string): string {
  return `claude mcp add --transport http kist ${origin.replace(/\/$/, '')}/mcp --header "Authorization: Bearer YOUR_TOKEN"`
}
