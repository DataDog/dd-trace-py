"""Minimal WSGI app for testing graceful shutdown with gevent + profiling.

Endpoints:
  GET /health  -> 200 "ok"          (readiness probe)
  GET /slow    -> 200 "slow-ok"     (sleeps ~5s, simulates in-flight work)
"""

from __future__ import annotations

import time
from typing import Callable


def app(
    environ: dict[str, str],
    start_response: Callable[[str, list[tuple[str, str]]], None],
) -> list[bytes]:
    path = environ.get("PATH_INFO", "/")

    if path == "/health":
        body = b"ok"
    elif path == "/slow":
        time.sleep(5)
        body = b"slow-ok"
    else:
        status = "404 Not Found"
        start_response(status, [("Content-Type", "text/plain")])
        return [b"not found"]

    start_response("200 OK", [("Content-Type", "text/plain")])
    return [body]
