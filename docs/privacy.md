# Privacy

Kist is built to keep your library at home. This page lists every case in
which Kist itself contacts something outside your network, and what it sends.

## Off by default (you turn them on)

| feature | setting | what leaves |
|---|---|---|
| Cloud captions | `KIST_CAPTION_PROVIDER=openai`, `anthropic` or `gemini` | every image you caption, to that company. Kist warns in `/api/ai/status` and the worker log. Ollama or an OpenAI-compatible server on your network keeps images at home |
| Link-rot watch | `KIST_LINK_CHECK_DAYS=7` (days) | that often, a request to each item's source page (one every 2 s, never the same host twice in 10 s) |
| Wayback lookups | `KIST_WAYBACK=1` (with the link-rot watch on) | the addresses of source pages that have gone, to archive.org |
| Server-side page snapshots | `KIST_SNAPSHOTS=1` | a request to the item's source page |
| Hosted map tiles | `KIST_MAP_TILES_URL` | which map areas you look at, to that tile provider (the default is a PMTiles file you host) |

## On by default

| feature | setting to turn off | what leaves |
|---|---|---|
| Model downloads | `KIST_ML_BASE_URL` (a mirror) | on first start of the `ml` service, the model files from Hugging Face (~600 MB); with `KIST_FACES=1`, the face models (~39 MB) from OpenCV's model zoo on GitHub (`KIST_FACE_MODELS_URL` for a mirror). All are checked by sha256 |

Things that happen only when you ask: **Add from URL** downloads that URL
(private and LAN addresses are refused unless `KIST_URL_ALLOW_PRIVATE` or
`KIST_URL_ALLOW_HOSTS` say otherwise), `kist geo-download` fetches GeoNames
files once, and importers fetch the images an export links to.

## Never

- No telemetry, analytics, crash reporting to anyone, or update checks. The web
  app reports its own errors only to your Kist server (`/api/client-errors`).
- No third-party fonts, scripts or CDNs in the web app; the docs site
  self-hosts its fonts too.
- Faces (opt-in) are found and matched on your server only.
- AI search and OCR run on your hardware.
- Credits auto-fill and the AI-made flag read only files already in your
  library (the original's metadata and a stored page snapshot); they never
  request a page.
- Kinds (Screenshots, Drawings …) are decided on your server from data it
  already has; corrections and what they teach stay in your library.

## Sharing

Share links (`/s/<slug>`) are the only pages that work without a token. They
show only that share's items (originals only if you allow it), with no script,
`noindex` and `Referrer-Policy: no-referrer`. Visitors' addresses aren't
logged. Expired or revoked links answer 410.

When you let guests pick and comment on a link (off by default), Kist keeps
what they typed (an optional display name, their picks, hearts and comments)
and a random guest id in `library/.kist/shares/<id>/feedback.json`. The id and
name ride in an HttpOnly cookie scoped to that link. Rate limits count actions
per visitor address in memory only (hashed, never written down). Nothing is
sent anywhere; no email goes out.

## Present

A show runs in your browser from your Kist server. The phone remote talks to
the same server only (a long-poll relay at `/present-relay/…`; nothing goes
to an outside service), with a pairing token that works for one show, ends
after `KIST_PRESENT_REMOTE_HOURS` (default 4), is revoked by Unpair or a
restart, and sits in the QR code's `#fragment`, so it never reaches a server
or proxy log. The server keeps only its SHA-256. "Keep this show offline"
stores that show's images in the browser's own cache on that device (one
show at a time, within the size you choose); the PDF is made on the server.

## Exports

Export presets strip metadata by default and GPS always. Portable exports keep
everything, because they're your backup in another shape.

## Your tokens

Only SHA-256 hashes of tokens are stored. The web app keeps your token in the
browser's local storage on that device; clearing the site's data in the browser removes it.
