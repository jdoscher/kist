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

# restic (Debian's package) for `kist backup`; pg_dump has to match the
# Postgres 16 server, so postgresql-client-16 comes from the PostgreSQL apt
# repository (bookworm's own client is 15)
# exiftool (libimage-exiftool-perl): camera metadata and the embedded
# previews of RAW files (Kist's own reader covers them when it's missing);
# Debian's ffmpeg includes ffprobe, libwebp and the VA-API/QSV encoders
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvips42 \
    ffmpeg \
    libimage-exiftool-perl \
    libmagic1 \
    restic \
    ca-certificates \
    curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md* ./
COPY kist/ ./kist/
COPY migrations/ ./migrations/
COPY alembic.ini docker-entrypoint.sh ./
RUN pip install ".[media]" && chmod +x docker-entrypoint.sh

COPY --from=web /web/dist ./web/dist

# never run as root: the decoders (ffmpeg, libvips, pdfium, libheif) parse
# files from anywhere. The library volume must be owned by this uid
# (override with `user:` in compose to match an existing library's owner).
RUN groupadd --system --gid 1000 kist \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin kist
USER 1000:1000

# the commit this image was built from (/api/version, deep health); last so
# a new commit doesn't invalidate the cached layers above
ARG KIST_GIT_COMMIT=unknown
ENV KIST_GIT_COMMIT=$KIST_GIT_COMMIT

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
