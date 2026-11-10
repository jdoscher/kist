// Wording for the notice after a bulk edit (pure, so it can be tested).

import type { BulkAction } from './bulk'

/** The notice after a bulk edit that went through in full (shown with
 * Undo, like Trash and Stack), or null for actions that have their own. */
export function bulkDoneText(action: BulkAction, n: number, folderName?: (id: string) => string | undefined): string | null {
  const items = n === 1 ? '1 item' : `${n} items`
  switch (action.kind) {
    case 'addTags':
      return `Tagged ${items} ${action.tags.map((t) => `#${t}`).join(' ')}.`
    case 'removeTag':
      return `Removed #${action.tag} from ${items}.`
    case 'addFolder':
      return `Added ${items} to “${folderName?.(action.folderId) ?? 'the folder'}”.`
    case 'move':
      return action.folderId === null ? `Took ${items} out of every folder.` : `Moved ${items} to “${folderName?.(action.folderId) ?? 'the folder'}”.`
    case 'protect':
      return action.value ? `Protected ${items}.` : `Unprotected ${items}.`
    case 'rating':
      return action.rating ? `Rated ${items} ${action.rating} star${action.rating === 1 ? '' : 's'}.` : `Cleared the rating on ${items}.`
    case 'flag':
      return action.flag ? `Marked ${items} as ${action.flag === 'pick' ? 'picked' : 'rejected'}.` : `Unflagged ${items}.`
    case 'label':
      return action.label ? `Labelled ${items} ${action.label}.` : `Cleared the label on ${items}.`
    default:
      return null // trash has its own notice; restore and delete forever have none
  }
}
