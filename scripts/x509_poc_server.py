#!/usr/bin/env python3
"""Local stand-in registration & live AI Guard config push server (POC).

Listens on 127.0.0.1:8080 (or --port / X509_POC_SERVER_PORT).

Endpoints:
  GET  /clients   - list registered tracers
  POST /register  - register a tracer's {connection_id, host_application, host_tags}
                    and push its scoped initial payload/config via staging rc-x509-cli
  POST /push      - push a live JSON AI Guard config (or raw string payload) to a
                    specific connection_id or all clients matching (service, env)
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

# Scoped initial configs pushed on registration when --mode=config (default in Phase 3).
# Empty rules/tool_policies let initial evaluations return FALLBACK_REMOTE until live rules are pushed.
_SCOPED_INITIAL_CONFIGS: dict[tuple[str, str], dict[str, Any]] = {
    ("poc-ai-guard", "staging"): {
        "enabled": True,
        "blocking": True,
        "rules": [],
        "tool_policies": {},
    },
}


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


def make_handler(datacenter: str, startup_mode: str):
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

                if startup_mode == "config":
                    scoped_cfg = _SCOPED_INITIAL_CONFIGS.get(
                        (service, env_name),
                        {"enabled": True, "blocking": True, "rules": [], "tool_policies": {}},
                    )
                    payload = json.dumps(scoped_cfg, separators=(",", ":"))
                    reason = "poc-startup-config"
                else:
                    payload = f"hello service={service} env={env_name} ts={int(time.time())}"
                    reason = "poc-startup"

                print(
                    f"[x509_poc_server] registered cid={cid} service={service} env={env_name}; "
                    f"pushing {reason} ({len(payload)} B)",
                    flush=True,
                )
                result = _push_via_cli(datacenter, cid, reason, payload)
                print(f"[x509_poc_server] startup push result: {json.dumps(result)}", flush=True)
                self._send_json(200 if result["ok"] else 502, result)
                return

            if self.path == "/push":
                target_cid = body.get("connection_id")
                target_service = body.get("service")
                target_env = body.get("env")
                reason = str(body.get("reason") or "poc-live-config")

                if "config" in body:
                    payload = json.dumps(body["config"], separators=(",", ":"))
                elif "payload" in body:
                    payload = str(body["payload"])
                else:
                    self._send_json(400, {"error": "provide 'config' (JSON object) or 'payload' (string)"})
                    return

                if len(payload.encode("utf-8")) > 1024:
                    self._send_json(400, {"error": f"payload exceeds 1024 B DebugService/Ping limit ({len(payload)} B)"})
                    return

                if target_cid:
                    cids = [str(target_cid)]
                else:
                    with _CLIENTS_LOCK:
                        cids = [
                            cid
                            for cid, meta in _CLIENTS.items()
                            if (not target_service or meta.get("service") == target_service)
                            and (not target_env or meta.get("env") == target_env)
                        ]

                if not cids:
                    self._send_json(404, {"error": "no matching registered clients"})
                    return

                results = [_push_via_cli(datacenter, cid, reason, payload) for cid in cids]
                all_ok = all(r["ok"] for r in results)
                self._send_json(200 if all_ok else 502, {"ok": all_ok, "pushed": len(results), "results": results})
                return

            self._send_json(404, {"error": "not found"})

    return PocHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Local RC x509 Pluggable Backend stand-in server")
    parser.add_argument("--host", default=os.environ.get("X509_POC_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("X509_POC_SERVER_PORT", "8080")))
    parser.add_argument("--datacenter", default=os.environ.get("DD_DATACENTER", "us1.staging.dog"))
    parser.add_argument(
        "--mode",
        choices=("config", "hello"),
        default=os.environ.get("X509_POC_SERVER_MODE", "config"),
        help="Initial startup payload mode: 'config' (Phase 3 JSON) or 'hello' (Phase 2 string)",
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.datacenter, args.mode))
    print(
        f"[x509_poc_server] listening on http://{args.host}:{args.port} "
        f"(datacenter={args.datacenter} mode={args.mode})",
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
