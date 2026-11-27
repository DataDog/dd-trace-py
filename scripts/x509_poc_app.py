#!/usr/bin/env python3
"""Minimal POC demo app for AI Guard RC x509 Magic Tunnel push.

Single-process CLI mode:
  dd-auth --domain dd.datad0g.com -- env \
    DD_SITE=datad0g.com DD_AI_GUARD_X509_ENABLED=true \
    ddtrace-run python scripts/x509_poc_app.py

Gunicorn pre-fork WSGI mode:
  dd-auth --domain dd.datad0g.com -- env \
    DD_SITE=datad0g.com DD_AI_GUARD_X509_ENABLED=true \
    ddtrace-run gunicorn -w 2 --bind 127.0.0.1:8000 scripts.x509_poc_app:app
"""

import json
import os
import signal
import sys
import time

from ddtrace.internal.aiguard.product import get_x509_client


def app(environ, start_response):
    client = get_x509_client()
    body = json.dumps(
        {
            "pid": os.getpid(),
            "connection_id": client.connection_id() if client else None,
            "payload": client.payload() if client else None,
        }
    ).encode("utf-8")
    start_response(
        "200 OK",
        [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
    )
    return [body]


def main() -> int:
    duration = float(os.environ.get("X509_POC_DURATION_SEC", "0"))
    client = get_x509_client()
    if client is None:
        print(
            "[x509_poc_app] AiGuardX509 client not running. "
            "Run under ddtrace-run with DD_AI_GUARD_X509_ENABLED=true and DD_API_KEY set.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    cid = client.wait_for_connection_id(timeout_ms=15000)
    print(f"[x509_poc_app] pid={os.getpid()} ready connection_id={cid}", flush=True)

    stop = False

    def _handle_sig(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    start_ts = time.monotonic()
    while not stop:
        if duration > 0 and (time.monotonic() - start_ts) >= duration:
            break
        time.sleep(0.25)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
