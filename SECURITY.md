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
  header, a `?token=` query parameter (convenient for curl, but query strings
  can end up in proxy logs), or the `kist_token` cookie the web UI sets. The
  cookie only authorizes GET/HEAD requests, so another site can't make
  state-changing requests that ride on it.
- **Untrusted files.** Originals are served with `X-Content-Type-Options:
  nosniff`. SVGs are served under a sandboxing Content-Security-Policy, and
  anything that isn't an image, video, or PDF is served as a download, so a
  stored file can't run script in Kist's origin.
- **Server-side downloads.** "Add from URL" makes the server fetch the URL
  you give it, which can include addresses on your internal network. Only
  token holders can do this, but keep it in mind if you share your token
  with an agent.
- **Agents.** The MCP tools can't permanently delete anything, and
  `add_item(file_path=…)` can only read from `library/import/` and
  `library/inbox/`.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: on the repository's
**Security** tab, choose **Report a vulnerability**. Please don't open a
public issue. You'll get a response within a week.
