#!/bin/sh
set -e

case "$1" in
  api)
    python -m kist.preflight  # the library must be writable (one clear line if not)
    python -m kist.migrate
    exec uvicorn kist.app:app --host 0.0.0.0 --port 8000
    ;;
  worker)
    python -m kist.preflight
    python -m kist.migrate
    exec python -m kist.worker
    ;;
  *)
    exec "$@"
    ;;
esac
