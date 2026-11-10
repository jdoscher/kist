// What the screen does after a new version is uploaded (pure, so it can be
// tested; ItemCreative.tsx applies it).

/** The server links the upload version_of the chain's newest and stacks the
 * chain with it as the cover, so: select the new version (the info panel
 * follows it), reload what the new stack changed, offer Compare. */
export function afterVersionUpload(r: { item: { id: string; filename: string }; created: boolean }, fromId: string) {
  const isNew = r.created && r.item.id !== fromId
  return {
    select: r.item.id,
    invalidate: ['item', 'stack', 'stackSuggestions', 'versions', 'links'] as const,
    message: isNew ? `Added “${r.item.filename}” as the newest version.` : `That file is already in the library (“${r.item.filename}”).`,
    compare: isNew ? ([fromId, r.item.id] as [string, string]) : null,
  }
}
