"""Logging, request ids, and what an unhandled error turns into.

Every log line carries a time, level, logger name and the id of the request
it belongs to; every response carries that id in X-Request-ID, and a 500
returns it in the body, so what someone saw in the browser leads straight
to the server's log line.
"""

import collections
import contextvars
import logging
import logging.config
import re
import threading
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
# a request id taken from the reverse proxy must look like one
_INCOMING_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

log = logging.getLogger("kist.http")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str | None = None) -> None:
    """One format for the app, the worker and uvicorn. KIST_LOG_LEVEL sets
    the level (default INFO)."""
    level = (level or get_settings().log_level or "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = "INFO"
    handler = {
        "class": "logging.StreamHandler",
        "formatter": "plain",
        "filters": ["request_id"],
    }
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": RequestIdFilter}},
            "formatters": {"plain": {"format": LOG_FORMAT}},
            "handlers": {"stderr": handler},
            "root": {"level": level, "handlers": ["stderr"]},
            "loggers": {
                "uvicorn": {"level": level, "handlers": ["stderr"], "propagate": False},
                "uvicorn.error": {"level": level, "handlers": [], "propagate": True},
                "uvicorn.access": {"level": level, "handlers": ["stderr"], "propagate": False},
                # libvips reports every resize step at INFO
                "pyvips": {"level": "WARNING"},
            },
        }
    )
    for name, logger in logging.root.manager.loggerDict.items():
        if name.startswith("kist") and isinstance(logger, logging.Logger):
            logger.disabled = False


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestContext:
    """Pure ASGI: give each request an id (the proxy's X-Request-ID if it
    sent a sane one), return it on every response, and turn an unhandled
    exception into a logged 500 that names it."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = ""
        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                incoming = value.decode("latin-1").strip()
                break
        rid = incoming if _INCOMING_ID.match(incoming) else new_request_id()
        token = request_id_var.set(rid)
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["X-Request-ID"] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            log.exception("unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            response = JSONResponse(
                {"detail": "Internal error", "request_id": rid},
                status_code=500,
                headers={"X-Request-ID": rid},
            )
            await response(scope, receive, send)
        finally:
            request_id_var.reset(token)


class RateLimiter:
    """At most `limit` events per `window` seconds (per process)."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._times: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._times and now - self._times[0] > self.window:
                self._times.popleft()
            if len(self._times) >= self.limit:
                return False
            self._times.append(now)
            return True
