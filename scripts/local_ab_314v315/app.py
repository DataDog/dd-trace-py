#!/usr/bin/env python3
"""Minimal Rapid smoke workload (stdlib only) for local 3.14 vs 3.15 profiling A/B.

Mirrors the GET paths in corpus.txt / rapid_python_http_smoke_test handlers
(factorial, wait, alloc, pure_mem, file-io, http, dynamic_code stub, asyncio_burst).
No Rapid/FastAPI deps. Start with DD_PROFILING_ENABLED=true.
"""

from __future__ import annotations

import array
import asyncio
import ctypes
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import math
import os
import sys
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlparse


_pymem_malloc = ctypes.pythonapi.PyMem_Malloc
_pymem_malloc.restype = ctypes.c_void_p
_pymem_malloc.argtypes = [ctypes.c_size_t]
_pymem_free = ctypes.pythonapi.PyMem_Free
_pymem_free.restype = None
_pymem_free.argtypes = [ctypes.c_void_p]

PREFIX: str = "/internal/rapid_python_http_smoke_test"


def _f(qs: dict[str, list[str]], key: str, default: float) -> float:
    vals: list[str] | None = qs.get(key)
    if not vals:
        return default
    return float(vals[0])


def handle_root() -> dict[str, Any]:
    return {"message": "Hello, world!", "python": sys.version, "executable": sys.executable}


def handle_factorial(duration: float) -> dict[str, Any]:
    n: int = 0
    result: int = 1
    s: str | None = None
    s_failed: bool = False
    start: float = time.monotonic()
    deadline: float = start + duration
    while time.monotonic() < deadline:
        n += 1
        result = math.factorial(n)
        try:
            s = str(result)
        except ValueError:
            s_failed = True
    return {"n": n, "last_s": str(s), "s_failed": s_failed, "duration": time.monotonic() - start}


def handle_wait(seconds: float) -> dict[str, Any]:
    # Sync sleep in a worker thread keeps the endpoint off-CPU like Rapid's
    # asyncio.sleep when driven by ThreadingHTTPServer.
    time.sleep(seconds)
    return {"waited_seconds": seconds}


def handle_file_io(duration: float) -> dict[str, Any]:
    n: int = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        path: str = tmp.name
    start: float = time.monotonic()
    deadline: float = start + duration
    result: str | None = None
    while time.monotonic() < deadline:
        n += 1
        content: str = f"smoke test file io {n}"
        with open(path, "a") as f:
            f.write(content)
        with open(path) as f:
            result = f.read()
    os.unlink(path)
    return {"written": n, "read": result, "duration": time.monotonic() - start}


def handle_alloc_pressure(duration: float) -> dict[str, Any]:
    deadline: float = time.monotonic() + duration
    n: int = 0
    while time.monotonic() < deadline:
        big_list: list[None] = [None] * 100_000
        big_dict: dict[int, int] = {i: i for i in range(10_000)}
        big_array: array.array[int] = array.array("Q", [0] * 131_072)
        n += 1
        del big_list, big_dict, big_array
    return {"iterations": n}


def handle_pure_mem(duration: float, size: int) -> dict[str, Any]:
    n: int = 0
    failures: int = 0
    start: float = time.monotonic()
    deadline: float = start + duration
    while time.monotonic() < deadline:
        buf: int | None = _pymem_malloc(size)
        if buf:
            _pymem_free(buf)
            n += 1
        else:
            failures += 1
    return {
        "iterations": n + failures,
        "successful_allocs": n,
        "failed_allocs": failures,
        "bytes_per_iter": size,
        "total_bytes_cycled": n * size,
        "duration": time.monotonic() - start,
    }


def handle_dynamic_code(duration: float) -> dict[str, Any]:
    # Lightweight stand-in for Jinja compile/exec (no jinja2 dep).
    n: int = 0
    start: float = time.monotonic()
    deadline: float = start + duration
    while time.monotonic() < deadline:
        n += 1
        src: str = f"def _f_{n}(x):\n    return x * {n}\n"
        ns: dict[str, Any] = {}
        exec(src, ns)  # nosec B102 — intentional smoke workload
        ns[f"_f_{n}"](n)
    return {"iterations": n, "duration": time.monotonic() - start}


async def _asyncio_burst(seconds: float) -> dict[str, Any]:
    """Exercise asyncio + named tasks (sys.monitoring / stack path on 3.15)."""

    async def short_task() -> None:
        await asyncio.sleep(seconds / 2.0)

    async def long_task() -> None:
        await asyncio.sleep(seconds)

    t1: asyncio.Task[None] = asyncio.create_task(short_task(), name="short_task")
    await asyncio.gather(t1, long_task())
    return {"asyncio_burst_seconds": seconds, "python": sys.version}


def dispatch(path: str) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    route: str = parsed.path.rstrip("/") or "/"
    qs: dict[str, list[str]] = parse_qs(parsed.query)

    if route == PREFIX:
        return 200, handle_root()
    if route == f"{PREFIX}/factorial":
        return 200, handle_factorial(_f(qs, "duration", 0.5))
    if route == f"{PREFIX}/wait":
        return 200, handle_wait(_f(qs, "seconds", 0.5))
    if route == f"{PREFIX}/file-io":
        return 200, handle_file_io(_f(qs, "duration", 0.5))
    if route == f"{PREFIX}/alloc_pressure":
        return 200, handle_alloc_pressure(_f(qs, "duration", 0.5))
    if route == f"{PREFIX}/pure_mem":
        size: int = int(qs.get("size", [str(1024 * 1024)])[0])
        return 200, handle_pure_mem(_f(qs, "duration", 0.5), size)
    if route == f"{PREFIX}/dynamic_code_generation":
        return 200, handle_dynamic_code(_f(qs, "duration", 0.5))
    if route == f"{PREFIX}/http":
        # Self-call root without httpx.
        return 200, {"url": PREFIX, "status_code": 200, "body": handle_root()}
    if route == f"{PREFIX}/asyncio_burst":
        return 200, asyncio.run(_asyncio_burst(_f(qs, "seconds", 0.2)))
    if route == "/healthz":
        return 200, {"ok": True, "python": sys.version}
    return 404, {"error": "not found", "path": route}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        code: int
        body: dict[str, Any]
        code, body = dispatch(self.path)
        payload: bytes = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    port: int = int(os.environ.get("PORT", "18400"))
    # Import after env is set so DD_* profiling config is live.
    if os.environ.get("DD_PROFILING_ENABLED", "").lower() in ("1", "true"):
        from ddtrace.profiling import Profiler  # type: ignore[attr-defined]

        prof: Any = Profiler()
        prof.start()
        print(f"profiler started pid={os.getpid()} py={sys.version.split()[0]}", flush=True)

    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"local smoke listening on 127.0.0.1:{port} py={sys.version.split()[0]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
