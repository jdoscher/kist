# Agents (MCP)

Kist has a Model Context Protocol endpoint built in: streamable HTTP at
`/mcp`, with the same bearer tokens as the API. An agent can search your
library, **see** the images (`view_item` hands the model the picture itself),
add things, tag them and file them. Whatever it does is one batch in Recent
changes, so one undo reverts it, and there is deliberately no tool that
deletes anything for good.

## Connect Claude Code

```bash
claude mcp add --transport http kist https://kist.example.com/mcp \
  --header "Authorization: Bearer <token>"
```

The first-run checklist in the web app shows this command with your server's
address filled in. For Claude Desktop and other MCP clients, add a remote
(streamable HTTP) server with that URL and an `Authorization: Bearer` header.

Give agents their own token with the `agent` scope (below), so the history
shows what the agent did under its name and you can revoke it on its own.

## Things to ask

- "Find the navy blazer photos I saved and file them into Menswear."
- "What did I save last week about kitchen lighting? Show me the best three."
- "Tag everything in Unfiled that's a screenshot of a receipt with `receipts`."
- "Make a moodboard from my picks in the Interiors folder, as a masonry grid."
- "Undo the filing you just did."

The server tells agents to pass your description of what something looks like
as a meaning search, so "moody dark interiors" works without Kist running a
language model of its own.

## Tools

| tool | |
|---|---|
| `search_items` | words, tags, folder, type, rating, flags, labels, people, dates, camera, place …; ranked by meaning (`semantic`), `similar_to`, `color` or `text` inside images |
| `search_by_image` | items that look like an image given by URL or a file under `library/import/` (nothing is saved) |
| `view_item`, `get_item`, `item_palette` | see the image, read its metadata, its colours |
| `add_item` | from a URL, or a file under `library/import/` or `library/inbox/` |
| `update_item`, `set_tags`, `set_folders`, `set_provenance` | organise: caption, flag, label, rating, protect, folders, credit and licence |
| `list_folders`, `create_folder`, `list_tags`, `list_smart_folders`, `get_vocabulary` | browse; read the vocabulary before tagging |
| `suggest_tags`, `tag_cleanup`, `merge_tags` | tag suggestions with reasons; cleanup as one undoable batch (`dry_run` first) |
| `duplicate_groups`, `resolve_duplicates`, `suggest_stacks`, `stack_items` | tidy duplicates and stacks |
| `list_boards`, `get_board`, `create_board`, `add_to_board`, `annotate` | moodboards and pinned notes |
| `list_shows`, `create_show` | saved Present shows: a folder, smart folder, board or chosen items as a full-screen show, with compare pairs; open one at its `web_url` |
| `view_video`, `save_frame`, `make_clip` | watch a video as a contact sheet, keep a frame, cut a clip |
| `export_collection`, `export_status`, `get_page_snapshot` | portable exports, the saved text of a source page |
| `recent_batches`, `undo_batch` | what changed lately, by whom, and undo it |
| `trash_item`, `restore_item` | into and out of the trash (emptying it is a human action in the web app) |

The README lists every tool with every parameter.

## Tokens

`KIST_TOKEN` is the owner's token (scope `admin`). Make narrower named tokens
in **Settings → Tokens** (or `POST /api/tokens`); the token is shown once, and
only its SHA-256 is stored.

| scope | can |
|---|---|
| `admin` | everything, including managing tokens |
| `write` | every REST edit, including permanent delete from the trash |
| `agent` | what MCP allows: edits, never a permanent delete, never unprotect |
| `add` | add items only (browser extension, iPhone Shortcut) |
| `read` | look only; with `folder_ids`, only those folders and their subfolders |
| `helper` | the AI job endpoints only (`kist helper` on a Mac) |

MCP accepts `admin`, `write`, `agent` and `read` tokens, and each tool checks
the scope: a read token can only search, get, view and list.
