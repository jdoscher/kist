# "Save to Kist" from the iPhone share sheet

A Shortcut that appears in the share sheet of Safari, Photos, Instagram,
Messages, Files and every other app, and sends what you share straight to
Kist without leaving the app you're in. It needs nothing new on the server:
`POST /api/items` already takes a file (multipart `file`) or a link
(`{"url": …}`), and answers in one line when asked for `text/plain`.

Kist can't hand you a ready-made `.shortcut` file: since iOS 15 a Shortcut
file only opens if Apple's servers signed it (`shortcuts sign` on a Mac),
which a server can't do. Building it takes about five minutes.
`GET /api/shortcut` (admin token) returns the same steps with your server's
address filled in.

## 1. Make an add-only token

The token lives on the phone, so give it the least it needs: the `add`
scope can create items and do nothing else, and can be revoked on its own.

```bash
curl -X POST https://kist.example.com/api/tokens \
  -H "Authorization: Bearer $KIST_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "iPhone", "scopes": ["add"]}'
```

Copy the `token` from the answer (`kist_…`); it is shown once. In the web
UI: Settings → Tokens → New token → scope *add*.

## 2. Build the Shortcut

In the Shortcuts app, tap **+**, name it **Save to Kist**, and add these
actions in order (search for each by name):

1. Open the Shortcut's settings (ⓘ): turn on **Show in Share Sheet**. Under
   *Share Sheet Types* keep **Images**, **Media**, **Files**, **PDFs**,
   **URLs**, **Safari web pages** and **Text**.
2. Set *Receive … from Share Sheet*; for *If there's no input* choose
   **Stop and Respond** with `Nothing to save`.
3. **Get URLs from Input** (input: *Shortcut Input*).
4. **If** *URLs* **has any value**:
   1. **Get Contents of URL**
      - URL: `https://kist.example.com/api/items`
      - Method: **POST**
      - Headers: `Authorization` = `Bearer kist_…` (the add-only token),
        `Accept` = `text/plain`
      - Request Body: **JSON**, with three Text fields:
        `url` = *URLs*, `title` = *Shortcut Input* → *Name*,
        `text` = *Shortcut Input* (as text)
5. **Otherwise**:
   1. **Get Contents of URL**
      - URL: `https://kist.example.com/api/items`
      - Method: **POST**
      - Headers: the same two
      - Request Body: **Form**, one **File** field: `file` = *Shortcut Input*
6. **End If**
7. **Show Notification**: *Contents of URL*.

Kist answers `Saved to Kist: <file name>` (201) for something new and
`Already in Kist: <file name>` (200) for an exact duplicate, so the
notification says what happened. A shared page link brings in the page's
preview image (`og:image`); its title becomes `provenance.page_title` and
any text you shared with it is added to the item's notes.

## Optional extras

- **Pick a folder:** before step 4 add **Get Contents of URL**
  `GET /api/folders` — this needs a token that can read, so either use a
  `read`+`add` token limited to the folders you file into
  (`{"scopes": ["read", "add"], "folder_ids": [...]}`), or hard-code folder
  ids in a **Choose from Menu**. Send the choice as `folder_ids` (JSON
  array for links; a form field `folder_ids` for files).
- **Tags or a note:** **Ask for Input**, then send it as `tags`
  (comma-separated) or `notes`.
- **Mark how it arrived:** add a JSON field
  `provenance` = `{"method": "extension"}` (or a form field with that JSON
  for files) if you want such saves told apart in Kist.

## When it fails

| answer | meaning |
|---|---|
| 401 | the token is wrong, revoked or expired |
| 403 | the token lacks the `add` scope |
| 413 | the file is bigger than `KIST_MAX_UPLOAD_BYTES` |
| 422 | a link Kist won't fetch (a private address, a page with no image), or bad input |
| 502 / 504 | the site refused or was too slow |

Over plain HTTP from outside your home network the phone can't reach Kist;
put it behind the TLS reverse proxy (see the README) and use that address.
