# Security

## Model

Kist is a single-user application. One shared bearer token (`KIST_TOKEN`)
grants full access to the API, the web UI, and the MCP endpoint. There are no
accounts or per-user permissions.

- **Use a strong token.** Kist refuses to start with an empty or placeholder
  token, or one under 16 characters. Generate one with `openssl rand -hex 24`.
- **Use TLS beyond your LAN.** Kist serves plain HTTP. Put it behind a
  reverse proxy that terminates TLS before exposing it to the internet.
- **Token transport.** The token is accepted as an `Authorization: Bearer`
  header or the `kist_token` cookie the web UI sets. The cookie only
  authorizes GET/HEAD requests, so another site can't make state-changing
  requests that ride on it. There is no `?token=` form, because query strings
  end up in access logs.
- **Browser hardening.** Every response carries a strict
  Content-Security-Policy (scripts only from Kist itself), `nosniff`,
  same-origin framing, a same-origin Referrer-Policy and HSTS
  (`KIST_HSTS_SECONDS`, on by default; browsers ignore it over plain HTTP).
- **Untrusted files.** Originals are served with `nosniff`. Opened on their
  own, originals are sandboxed documents (`Content-Security-Policy: sandbox`),
  and anything that isn't an image, video, audio file or PDF downloads
  instead of opening, so a stored file can't run script in Kist's origin.
  What counts is the type sniffed from the file's bytes, never its name: an
  HTML page named `invoice.pdf` is a sandboxed download. Real PDFs are the
  exception to the sandbox: Chromium-based browsers won't render a PDF in a
  sandboxed document. Their built-in viewers run PDF script isolated from
  the page's origin. Direct uploads are capped at `KIST_MAX_UPLOAD_BYTES`
  (4 GiB by default).
- **Network exposure.** Compose publishes Kist on every interface by default
  (`KIST_BIND=0.0.0.0`), which lets anything on the LAN reach it over plain
  HTTP and see the token in transit. When the TLS reverse proxy runs on the
  same host, set `KIST_BIND=127.0.0.1`. Set your own `KIST_DB_PASSWORD` too:
  Postgres publishes no port, but the default password is public. The API
  docs (`/docs`, `/openapi.json`) need the token.
- **Decoders.** Previews are generated in a child process with a time limit,
  so a malformed file can crash or hang only its own preview. PDF posters are
  rendered at a capped size, and ffmpeg only reads local files in the
  container format the file's extension names. The containers run as an
  unprivileged user with a read-only root filesystem, no Linux capabilities,
  no privilege escalation, and memory and process limits.
- **Server-side downloads.** "Add from URL" refuses private, loopback,
  link-local, CGNAT, multicast and unique-local addresses, including through
  redirects or a page's preview image, and connects to the exact address it
  checked. Allow trusted LAN sources with `KIST_URL_ALLOW_HOSTS` (or all of
  them with `KIST_URL_ALLOW_PRIVATE=true`). URL adds keep only images, videos
  and PDFs, downloads have an overall time limit, and network errors are
  reported without detail so they can't be used to map your network.
- **Agents.** The MCP tools can't permanently delete anything, and
  `add_item(file_path=…)` can only read from `library/import/` and
  `library/inbox/`.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: on the repository's
**Security** tab, choose **Report a vulnerability**. Please don't open a
public issue. You'll get a response within a week.
