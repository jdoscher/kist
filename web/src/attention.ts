import type { Stats } from './api'

/** What the sidebar's "Needs attention" row counts: things only you can fix
 * (failed imports, previews that couldn't be made, originals that no longer
 * match their fingerprint). A source page that moved or went away is not one
 * of them: Kist has its copy, and the item's info panel shows the page's
 * status for reference. */
export function attentionCount(
  stats: Pick<Stats, 'failed_jobs' | 'preview_failed' | 'fixity_failed'> | null | undefined,
): number {
  return (stats?.failed_jobs ?? 0) + (stats?.preview_failed ?? 0) + (stats?.fixity_failed ?? 0)
}
