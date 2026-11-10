# Install

Kist runs as one Docker Compose stack on any Linux box, NAS or Mac with
Docker. It needs about 2 GB of memory for the AI service plus what the app and
worker use, and disk for your library.

## Quick start

```bash
git clone https://github.com/jdoscher/kist.git && cd kist
cp .env.example .env
# set KIST_TOKEN in .env, e.g. to the output of: openssl rand -hex 24
mkdir library && sudo chown 1000:1000 library   # Kist runs as uid 1000
docker compose up -d --build
```

Open `http://<host>:8080`, paste your token, and drop files in.

!!! note "Make `library/` before the first start"
    Kist runs as uid/gid 1000 (`KIST_UID` / `KIST_GID` in `.env` change
    that). If Docker creates `./library` itself, it belongs to root and the
    app and worker stop at start with one line saying which `chown` to run.

On first start the `ml` service downloads its model files (~600 MB, checked by
sha256) into the `ml-models` volume. AI jobs wait for it; everything else works
at once.

| service | role |
|---|---|
| `app` | REST API, the MCP endpoint at `/mcp`, and the web app |
| `worker` | same image: ingests inbox drops, makes previews, runs background jobs |
| `db` | Postgres 16 with pgvector: the search index (the library on disk is the durable copy) |
| `ml` | internal only: image embeddings (OpenCLIP ViT-B-32) and OCR (RapidOCR) on ONNX Runtime |

Check it with `docker compose exec worker kist selftest` (every decoder should
say ✓) and `curl -H "Authorization: Bearer $KIST_TOKEN" http://localhost:8080/api/health/deep`.

## Reverse proxy and HTTPS

Kist speaks plain HTTP. To reach it from outside your LAN, put it behind a
TLS-terminating reverse proxy (Caddy, nginx, Traefik), set `KIST_BIND=127.0.0.1`
when the proxy runs on the same host, and set `KIST_PUBLIC_URL` to the address
you open it at. Over HTTPS the Paste button can read the clipboard directly,
and the app installs to a phone's Home Screen.

A minimal Caddyfile:

```
kist.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Kist is built for one person on a trusted network. Read
[SECURITY.md](https://github.com/jdoscher/kist/blob/main/SECURITY.md) before
exposing it to the internet.

## Next

- [Settings](settings.md): every `.env` option.
- [AI search and captions](ai.md).
- [Connect Claude or another agent](mcp.md).
- [Put it on your phone](phone.md).
- [Set up backups](backup.md).
