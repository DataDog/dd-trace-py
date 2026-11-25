#!/usr/bin/env python3
"""Minimal POC demo app for AI Guard RC x509 Magic Tunnel push & local eval.

Single-process CLI mode:
  dd-auth --domain dd.datad0g.com -- env \
    DD_SITE=datad0g.com DD_AI_GUARD_X509_ENABLED=true \
    ddtrace-run python scripts/x509_poc_app.py

Gunicorn pre-fork WSGI mode:
  dd-auth --domain dd.datad0g.com -- env \
    DD_SITE=datad0g.com DD_AI_GUARD_X509_ENABLED=true \
    ddtrace-run gunicorn -w 2 --bind 127.0.0.1:8000 scripts.x509_poc_app:app

WSGI Endpoints:
  GET  /       -> {pid, connection_id, payload}
  POST /eval   -> local evaluate(content, tool_name) + AIGuardClient pre-check demo
"""

import json
import os
import signal
import sys
import time
from urllib.parse import parse_qs

from ddtrace.internal.aiguard.product import get_x509_client


def _eval_with_aiguard_client(content: str, tool_name: str | None) -> dict:
    from ddtrace.aiguard import AIGuardAbortError
    from ddtrace.aiguard import AIGuardClient
    from ddtrace.aiguard import Message

    # Point to an unreachable local URL to prove local verdicts never invoke remote /evaluate.
    ai_client = AIGuardClient(
        api_key="poc-dummy-api-key",
        app_key="poc-dummy-app-key",
        endpoint="http://127.0.0.1:1/unreachable",
    )
    messages: list[Message] = [{"role": "user", "content": content}]
    if tool_name:
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": tool_name, "arguments": "{}"},
                    }
                ],
            }
        )

    t0 = time.perf_counter()
    try:
        ev = ai_client.evaluate(messages)
        elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
        return {
            "short_circuited": True,
            "aborted": False,
            "action": ev["action"],
            "reason": ev.get("reason"),
            "eval_us": round(elapsed_us, 1),
        }
    except AIGuardAbortError as exc:
        elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
        return {
            "short_circuited": True,
            "aborted": True,
            "action": exc.action,
            "reason": exc.reason,
            "eval_us": round(elapsed_us, 1),
        }
    except Exception as exc:
        elapsed_us = (time.perf_counter() - t0) * 1_000_000.0
        return {
            "short_circuited": False,
            "fell_back_to_remote": True,
            "remote_error": f"{type(exc).__name__}: {exc}",
            "eval_us": round(elapsed_us, 1),
        }


def app(environ, start_response):
    client = get_x509_client()
    path = environ.get("PATH_INFO", "/")

    if path == "/eval":
        content = ""
        tool_name = None
        if environ.get("REQUEST_METHOD") == "POST":
            length = int(environ.get("CONTENT_LENGTH") or "0")
            raw = environ["wsgi.input"].read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            content = str(data.get("content", ""))
            tool_name = data.get("tool_name")
        else:
            qs = parse_qs(environ.get("QUERY_STRING", ""))
            content = (qs.get("content") or [""])[0]
            tool_name = (qs.get("tool_name") or [None])[0]

        t0 = time.perf_counter()
        decision = client.evaluate(content, tool_name) if client else None
        native_eval_us = (time.perf_counter() - t0) * 1_000_000.0
        sdk_result = _eval_with_aiguard_client(content, tool_name)

        body = json.dumps(
            {
                "pid": os.getpid(),
                "connection_id": client.connection_id() if client else None,
                "native": (
                    {
                        "action": decision.action,
                        "rule_id": decision.rule_id,
                        "blocking": decision.blocking,
                        "eval_us": round(native_eval_us, 1),
                    }
                    if decision is not None
                    else None
                ),
                "aiguard_client": sdk_result,
            }
        ).encode("utf-8")
        start_response(
            "200 OK",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]

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
