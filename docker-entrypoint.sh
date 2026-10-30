#!/bin/sh
set -e

case "$1" in
  api)
    python -m kist.migrate
    exec uvicorn kist.app:app --host 0.0.0.0 --port 8000
    ;;
  worker)
    python -m kist.migrate
    exec python -m kist.worker
    ;;
  *)
    exec "$@"
    ;;
esac
