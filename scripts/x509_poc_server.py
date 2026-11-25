#!/usr/bin/env python3
"""Local stand-in registration server for the RC x509 Pluggable Backend POC.

Listens on 127.0.0.1:8080 (or --port / X509_POC_SERVER_PORT).
On POST /register with {connection_id, host_application, host_tags}, pushes a
startup Magic Tunnel DebugService/Ping payload via staging rc-x509-cli with
bounded retry on transient routing-table NotFound races.

Usage:
  python scripts/x509_poc_server.py [--host 127.0.0.1] [--port 8080] [--datacenter us1.staging.dog]
"""

import argparse
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
import subprocess
import threading
import time
from typing import Any


_CLIENTS_LOCK = threading.Lock()
_CLIENTS: dict[str, dict[str, Any]] = {}


def _extract_tag(host_tags: list[str], prefix: str, default: str = "unknown") -> str:
    needle = f"{prefix}:"
    for tag in host_tags:
        if isinstance(tag, str) and tag.startswith(needle):
            return tag[len(needle) :] or default
    return default


def _push_via_cli(
    datacenter: str,
    connection_id: str,
    reason: str,
    payload: str,
    max_attempts: int = 6,
) -> dict[str, Any]:
    cmd = [
        "rc-x509-cli",
        "--datacenter",
        datacenter,
        "debug",
        "ping",
        "--connection-id",
        connection_id,
        "--reason",
        reason,
        "--payload",
        payload,
    ]
    env = dict(os.environ, DDTOOL_TLS_PROBE_SLOW="true")
    backoff = 0.25
    t0 = time.monotonic()
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=20)
        if res.returncode == 0:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            return {
                "ok": True,
                "connection_id": connection_id,
                "attempts": attempt,
                "elapsed_ms": round(elapsed_ms, 1),
                "pong": res.stdout.strip(),
                "payload": payload,
            }
        last_err = (res.stderr or res.stdout).strip()
        is_transient = (
            "not found" in last_err.lower()
            or "unavailable" in last_err.lower()
            or "routing table" in last_err.lower()
        )
        print(
            f"[x509_poc_server] push attempt={attempt}/{max_attempts} cid={connection_id} "
            f"transient={is_transient} err={last_err}",
            flush=True,
        )
        if not is_transient or attempt == max_attempts:
            break
        time.sleep(backoff)
        backoff = min(backoff * 2.0, 2.0)

    elapsed_ms = (time.monotonic() - t0) * 1000.0
    return {
        "ok": False,
        "connection_id": connection_id,
        "attempts": max_attempts,
        "elapsed_ms": round(elapsed_ms, 1),
        "error": last_err,
    }


def make_handler(datacenter: str):
    class PocHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[x509_poc_server] {self.address_string()} - {fmt % args}", flush=True)

        def do_GET(self) -> None:
            if self.path == "/clients":
                with _CLIENTS_LOCK:
                    snapshot = dict(_CLIENTS)
                self._send_json(200, {"clients": snapshot})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                self._send_json(400, {"error": f"invalid JSON: {exc}"})
                return

            if self.path == "/register":
                cid = str(body.get("connection_id", "")).strip()
                if not cid:
                    self._send_json(400, {"error": "missing connection_id"})
                    return
                host_tags = body.get("host_tags") or []
                service = _extract_tag(host_tags, "service")
                env_name = _extract_tag(host_tags, "env")
                with _CLIENTS_LOCK:
                    _CLIENTS[cid] = {
                        "connection_id": cid,
                        "host_application": body.get("host_application"),
                        "host_tags": host_tags,
                        "service": service,
                        "env": env_name,
                        "registered_at": time.time(),
                    }
                payload = f"hello service={service} env={env_name} ts={int(time.time())}"
                print(
                    f"[x509_poc_server] registered cid={cid} service={service} env={env_name}; pushing startup payload",
                    flush=True,
                )
                result = _push_via_cli(datacenter, cid, "poc-startup", payload)
                print(f"[x509_poc_server] startup push result: {json.dumps(result)}", flush=True)
                self._send_json(200 if result["ok"] else 502, result)
                return

            self._send_json(404, {"error": "not found"})

    return PocHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Local RC x509 Pluggable Backend stand-in server")
    parser.add_argument("--host", default=os.environ.get("X509_POC_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("X509_POC_SERVER_PORT", "8080")))
    parser.add_argument("--datacenter", default=os.environ.get("DD_DATACENTER", "us1.staging.dog"))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.datacenter))
    print(
        f"[x509_poc_server] listening on http://{args.host}:{args.port} (datacenter={args.datacenter})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
