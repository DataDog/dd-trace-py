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
  GET  /            -> {pid, connection_id, payload}
  POST /eval        -> local evaluate(content, tool_name) + AIGuardClient pre-check demo
  POST /vault-chat  -> local mock OpenAI call proving zero-knowledge LLM + LLMObs egress
"""

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
import re
import signal
import sys
import threading
import time
from urllib.parse import parse_qs

from ddtrace.internal.aiguard.product import get_x509_client


_MOCK_LOCK = threading.Lock()
_MOCK_BASE_URL: str | None = None
_LAST_MOCK_REQUEST: dict | None = None
_TOKEN_RE = re.compile(r"<[A-Z0-9_]+>")


def _ensure_mock_openai_server() -> str:
    global _MOCK_BASE_URL
    with _MOCK_LOCK:
        if _MOCK_BASE_URL is not None:
            return _MOCK_BASE_URL

        class _MockOpenAIHandler(BaseHTTPRequestHandler):
            def log_message(self, _fmt, *_args):
                return

            def do_POST(self):
                global _LAST_MOCK_REQUEST
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                req_json = json.loads(raw.decode("utf-8"))
                _LAST_MOCK_REQUEST = req_json

                user_texts = [
                    m.get("content", "")
                    for m in req_json.get("messages", [])
                    if isinstance(m.get("content"), str)
                ]
                joined = " ".join(user_texts)
                tokens = _TOKEN_RE.findall(joined)
                if tokens:
                    reply = "Drafted confirmation referencing " + ", ".join(tokens) + "."
                    tool_args = json.dumps({"payload": " ".join(tokens), "dest": "external"})
                else:
                    reply = f"Drafted confirmation for: {joined}"
                    tool_args = json.dumps({"payload": joined, "dest": "external"})

                resp_obj = {
                    "id": "chatcmpl-poc-vault",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": req_json.get("model", "gpt-4o-mini"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": reply,
                                "tool_calls": [
                                    {
                                        "id": "call_webhook_1",
                                        "type": "function",
                                        "function": {
                                            "name": "send_external_webhook",
                                            "arguments": tool_args,
                                        },
                                    },
                                    {
                                        "id": "call_internal_2",
                                        "type": "function",
                                        "function": {
                                            "name": "internal_receipt_log",
                                            "arguments": tool_args,
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30},
                }
                body = json.dumps(resp_obj).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockOpenAIHandler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, name="poc-mock-openai", daemon=True).start()
        _MOCK_BASE_URL = f"http://127.0.0.1:{port}/v1"
        return _MOCK_BASE_URL


def _run_vault_chat(messages: list[dict]) -> dict:
    import openai

    from ddtrace import patch
    from ddtrace.aiguard._vault import get_last_vault_metrics
    from ddtrace.aiguard._vault import register_vault_listeners
    from ddtrace.internal import core

    patch(openai=True)
    register_vault_listeners()

    # Capture the exact event recorded when ddtrace finishes the OpenAI span
    # (before post-span re-hydration) to prove LLMObs stays zero-knowledge.
    captured_span: dict = {}

    def _capture_span_end(ctx, _exc_info=None):
        ev = getattr(ctx, "event", None)
        if ev is None:
            return
        kw = getattr(ev, "request_kwargs", None) or {}
        resp = getattr(ev, "response", None)
        out_msgs = []
        if resp is not None:
            for ch in getattr(resp, "choices", None) or []:
                m = getattr(ch, "message", None)
                if m is not None:
                    out_msgs.append(
                        {
                            "role": getattr(m, "role", None),
                            "content": getattr(m, "content", None),
                            "tool_calls": [
                                {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                }
                                for tc in (getattr(m, "tool_calls", None) or [])
                            ],
                        }
                    )
        captured_span["input_messages"] = kw.get("messages")
        captured_span["output_messages"] = out_msgs

    from ddtrace.contrib._events.llm import LlmRequestEvent

    ended_event = f"context.ended.{LlmRequestEvent.event_name}"
    core.on(ended_event, _capture_span_end)
    try:
        base_url = _ensure_mock_openai_server()
        oai = openai.OpenAI(api_key="poc-mock-key", base_url=base_url)
        resp = oai.chat.completions.create(model="gpt-4o-mini", messages=messages)
    finally:
        core.reset_listeners(ended_event, _capture_span_end)

    choice_msg = resp.choices[0].message
    return {
        "outbound_to_mock_llm": _LAST_MOCK_REQUEST,
        "llmobs_span_captured": captured_span,
        "app_received_response": {
            "content": choice_msg.content,
            "tool_calls": [
                {"name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (choice_msg.tool_calls or [])
            ],
        },
        "vault_metrics": get_last_vault_metrics(),
    }


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

    if path == "/vault-chat":
        messages = [{"role": "user", "content": ""}]
        if environ.get("REQUEST_METHOD") == "POST":
            length = int(environ.get("CONTENT_LENGTH") or "0")
            raw = environ["wsgi.input"].read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            if "messages" in data and isinstance(data["messages"], list):
                messages = data["messages"]
            elif "content" in data:
                messages = [{"role": "user", "content": str(data["content"])}]

        chat_res = _run_vault_chat(messages)
        body = json.dumps(
            {
                "pid": os.getpid(),
                "connection_id": client.connection_id() if client else None,
                "sds_active": client.sds_active() if client else False,
                **chat_res,
            }
        ).encode("utf-8")
        start_response(
            "200 OK",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]

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
