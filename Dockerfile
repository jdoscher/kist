# ---- web build ----
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- app ----
FROM python:3.12-slim-bookworm
# HOME/XDG in /tmp: the container runs with a read-only root filesystem and
# /tmp as its only writable path outside the library
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp XDG_CACHE_HOME=/tmp/.cache

RUN apt-get update && apt-get install -y --no-install-recommends \
    libvips42 \
    ffmpeg \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md* ./
COPY kist/ ./kist/
COPY migrations/ ./migrations/
COPY alembic.ini docker-entrypoint.sh ./
RUN pip install . && chmod +x docker-entrypoint.sh

COPY --from=web /web/dist ./web/dist

# never run as root: the decoders (ffmpeg, libvips, pdfium, libheif) parse
# files from anywhere. The library volume must be owned by this uid
# (override with `user:` in compose to match an existing library's owner).
RUN groupadd --system --gid 1000 kist \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin kist
USER 1000:1000

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
