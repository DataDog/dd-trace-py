#!/usr/bin/env python3
"""Long-lived asyncio event-loop workload for local 3.14 vs 3.15 profiling A/B.

Unlike ``app.py`` (ThreadingHTTPServer + per-request ``asyncio.run``), this keeps
one event loop alive for the soak window with continuous ``create_task`` churn,
nested awaits (CPU + sleep), named tasks, parent/child linking stress, and
optional TaskGroup / gather fan-out.

Stdlib only. Start with ``DD_PROFILING_ENABLED=true``.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import sys
import time
from typing import Any
from typing import cast


HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 18500

# Background churn knobs (overridable via env).
CHURN_INTERVAL_S: float = float(os.environ.get("ASYNC_CHURN_INTERVAL_S", "0.05"))
CHURN_BATCH: int = int(os.environ.get("ASYNC_CHURN_BATCH", "8"))
NEST_DEPTH: int = int(os.environ.get("ASYNC_NEST_DEPTH", "4"))
FANOUT_WIDTH: int = int(os.environ.get("ASYNC_FANOUT_WIDTH", "6"))
LONG_TASK_POOL: int = int(os.environ.get("ASYNC_LONG_TASK_POOL", "24"))
CPU_ITERS: int = int(os.environ.get("ASYNC_CPU_ITERS", "800"))

_stats: dict[str, Any] = {
    "churn_batches": 0,
    "tasks_spawned": 0,
    "fanouts": 0,
    "http_ok": 0,
    "started_monotonic": 0.0,
}
_stats_lock: asyncio.Lock | None = None


def _cpu_burst(iters: int) -> int:
    """Busy-work so stack samples can land inside named tasks."""
    acc: int = 0
    for i in range(iters):
        acc += math.factorial(min(i % 12, 10))
    return acc


async def _leaf_work(name: str, sleep_s: float, cpu_iters: int) -> dict[str, Any]:
    await asyncio.sleep(sleep_s)
    cpu: int = await asyncio.to_thread(_cpu_burst, cpu_iters)
    return {"name": name, "cpu": cpu, "sleep_s": sleep_s}


async def _nested_await(depth: int, prefix: str) -> dict[str, Any]:
    """Nested awaits + create_task for parent/child linking stress."""
    if depth <= 0:
        return await _leaf_work(f"{prefix}-leaf", 0.01, CPU_ITERS // 4)

    child_name: str = f"{prefix}-d{depth}"
    child: asyncio.Task[dict[str, Any]] = asyncio.create_task(
        _nested_await(depth - 1, child_name),
        name=child_name,
    )
    sibling: asyncio.Task[dict[str, Any]] = asyncio.create_task(
        _leaf_work(f"{prefix}-sib{depth}", 0.005, CPU_ITERS // 8),
        name=f"{prefix}-sib{depth}",
    )
    results: list[dict[str, Any]] = list(await asyncio.gather(child, sibling))
    return {"depth": depth, "children": results}


async def _fanout_gather(width: int, prefix: str) -> dict[str, Any]:
    tasks: list[asyncio.Task[dict[str, Any]]] = [
        asyncio.create_task(
            _leaf_work(f"{prefix}-g{i}", 0.01 + (i % 3) * 0.005, CPU_ITERS // 6),
            name=f"{prefix}-g{i}",
        )
        for i in range(width)
    ]
    gathered: list[dict[str, Any]] = list(await asyncio.gather(*tasks))
    return {"width": width, "n": len(gathered)}


async def _fanout_taskgroup(width: int, prefix: str) -> dict[str, Any]:
    # TaskGroup is 3.11+; mypy may run on an older stubs baseline.
    task_group_cls: Any = getattr(asyncio, "TaskGroup", None)
    if task_group_cls is None:
        return await _fanout_gather(width, prefix)
    results: list[dict[str, Any]] = []
    async with task_group_cls() as tg:
        for i in range(width):
            t: asyncio.Task[dict[str, Any]] = cast(
                "asyncio.Task[dict[str, Any]]",
                tg.create_task(
                    _leaf_work(f"{prefix}-tg{i}", 0.01, CPU_ITERS // 6),
                    name=f"{prefix}-tg{i}",
                ),
            )
            results.append({"scheduled": t.get_name()})
    return {"width": width, "scheduled": len(results)}


async def _long_lived_task(idx: int) -> None:
    """Stay runnable for the soak so asyncio_task_count stays elevated."""
    while True:
        await asyncio.sleep(0.5 + (idx % 5) * 0.05)
        _ = _cpu_burst(40)


async def _churn_loop() -> None:
    """Continuous create_task churn for the whole process lifetime."""
    global _stats_lock
    lock: asyncio.Lock | None = _stats_lock
    if lock is None:
        raise RuntimeError("stats lock not initialized")
    n: int = 0
    while True:
        batch_id: int = n
        n += 1
        tasks: list[asyncio.Task[Any]] = []
        for i in range(CHURN_BATCH):
            name: str = f"churn-{batch_id}-{i}"
            if i % 3 == 0:
                tasks.append(asyncio.create_task(_nested_await(NEST_DEPTH, name), name=name))
            elif i % 3 == 1:
                tasks.append(asyncio.create_task(_fanout_gather(FANOUT_WIDTH, name), name=name))
            else:
                tasks.append(asyncio.create_task(_fanout_taskgroup(max(2, FANOUT_WIDTH // 2), name), name=name))
        await asyncio.gather(*tasks, return_exceptions=True)
        async with lock:
            _stats["churn_batches"] = int(_stats["churn_batches"]) + 1
            _stats["tasks_spawned"] = int(_stats["tasks_spawned"]) + len(tasks)
        await asyncio.sleep(CHURN_INTERVAL_S)


def _task_count() -> int:
    try:
        return len(asyncio.all_tasks())
    except RuntimeError:
        return 0


async def _stats_snapshot() -> dict[str, Any]:
    global _stats_lock
    lock: asyncio.Lock | None = _stats_lock
    if lock is None:
        raise RuntimeError("stats lock not initialized")
    async with lock:
        snap: dict[str, Any] = dict(_stats)
    snap["asyncio_task_count"] = _task_count()
    snap["uptime_s"] = time.monotonic() - float(snap["started_monotonic"])
    snap["python"] = sys.version
    names: list[str] = []
    for t in asyncio.all_tasks():
        nm: str = t.get_name()
        if nm and not nm.startswith("Task-"):
            names.append(nm)
    snap["named_tasks_sample"] = sorted(set(names))[:40]
    snap["named_tasks_n"] = len(set(names))
    return snap


def _hook_path_probe() -> dict[str, Any]:
    """Report whether create_task uses wrap() (<3.15) or sys.monitoring (3.15+).

    Mirrors tests/profiling/collector/test_asyncio_wrap_path.py expectations so the
    long-lived async A/B harness can assert registration path without a 90s soak.
    """
    expected: str = "monitoring" if sys.version_info >= (3, 15) else "wrap"
    out: dict[str, Any] = {
        "python": sys.version.split()[0],
        "hexversion": hex(sys.hexversion),
        "expected_path": expected,
        "profiling_env": os.environ.get("DD_PROFILING_ENABLED", ""),
        "asyncio_imported": False,
        "create_task_wrapped": None,
        "tg_create_task_wrapped": None,
        "monitoring_tool_id": None,
        "create_task_handler_registered": False,
        "tg_create_task_handler_registered": False,
        "observed_path": "unknown",
        "ok": False,
        "error": None,
    }
    try:
        from types import FunctionType
        from typing import cast

        from ddtrace.internal.wrapping import is_wrapped
        from ddtrace.profiling import _asyncio

        # Private registration state is not in stubs; probe via getattr.
        aio_mod: Any = _asyncio
        handlers: dict[int, Any] = dict(getattr(aio_mod, "_py_return_handlers", {}) or {})
        tool_id_raw: Any = getattr(aio_mod, "_monitoring_tool_id", None)
        tool_id: int | None = int(tool_id_raw) if tool_id_raw is not None else None

        create_task_fn: FunctionType = cast(FunctionType, asyncio.tasks.create_task)
        create_wrapped: bool = bool(is_wrapped(create_task_fn))
        create_handler: bool = id(create_task_fn.__code__) in handlers

        tg_wrapped: bool | None = None
        tg_handler: bool = False
        taskgroups: Any = sys.modules.get("asyncio.taskgroups")
        if taskgroups is not None and hasattr(taskgroups.TaskGroup, "create_task"):
            tg_fn: FunctionType = cast(FunctionType, taskgroups.TaskGroup.create_task)
            tg_wrapped = bool(is_wrapped(tg_fn))
            tg_handler = id(tg_fn.__code__) in handlers

        observed: str
        if create_wrapped and tool_id is None and not create_handler:
            observed = "wrap"
        elif (not create_wrapped) and tool_id is not None and create_handler:
            observed = "monitoring"
        else:
            observed = "unknown"

        ok: bool = observed == expected
        if expected == "wrap" and tg_wrapped is False:
            ok = False
        if expected == "monitoring" and (tg_wrapped is True or not tg_handler):
            ok = False

        out.update(
            {
                "asyncio_imported": bool(aio_mod.ASYNCIO_IMPORTED),
                "create_task_wrapped": create_wrapped,
                "tg_create_task_wrapped": tg_wrapped,
                "monitoring_tool_id": tool_id,
                "create_task_handler_registered": create_handler,
                "tg_create_task_handler_registered": tg_handler,
                "observed_path": observed,
                "ok": ok,
            }
        )
    except Exception as exc:  # noqa: BLE001 — probe must always return JSON
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["ok"] = False
    return out


async def _dispatch(path: str) -> tuple[int, dict[str, Any]]:
    global _stats_lock
    route: str = path.split("?", 1)[0].rstrip("/") or "/"
    if route == "/healthz":
        return 200, {"ok": True, "python": sys.version, "tasks": _task_count()}
    if route == "/hook_path":
        probe: dict[str, Any] = _hook_path_probe()
        return (200 if probe.get("ok") else 500), probe
    if route == "/stats":
        return 200, await _stats_snapshot()
    if route == "/work":
        body: dict[str, Any] = await _nested_await(NEST_DEPTH, "http-work")
        return 200, {"ok": True, "result": body, "tasks": _task_count()}
    if route == "/churn":
        body = await _fanout_gather(FANOUT_WIDTH, "http-churn")
        lock: asyncio.Lock | None = _stats_lock
        if lock is None:
            raise RuntimeError("stats lock not initialized")
        async with lock:
            _stats["fanouts"] = int(_stats["fanouts"]) + 1
        return 200, {"ok": True, "result": body, "tasks": _task_count()}
    if route == "/fanout":
        body = await _fanout_taskgroup(FANOUT_WIDTH, "http-fanout")
        return 200, {"ok": True, "result": body, "tasks": _task_count()}
    return 404, {"error": "not found", "path": route}


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    global _stats_lock
    try:
        raw: bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30.0)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        await writer.wait_closed()
        return

    first: str = raw.decode("latin-1", errors="replace").split("\r\n", 1)[0]
    parts: list[str] = first.split()
    path: str = parts[1] if len(parts) >= 2 else "/"
    code: int
    body: dict[str, Any]
    try:
        code, body = await _dispatch(path)
    except Exception as exc:  # noqa: BLE001 — surface as 500 for the driver
        code, body = 500, {"error": type(exc).__name__, "detail": str(exc)}

    payload: bytes = json.dumps(body).encode()
    header: bytes = (
        f"HTTP/1.1 {code} {'OK' if code == 200 else 'ERR'}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode()
    writer.write(header + payload)
    await writer.drain()
    writer.close()
    await writer.wait_closed()
    if code == 200 and _stats_lock is not None:
        async with _stats_lock:
            _stats["http_ok"] = int(_stats["http_ok"]) + 1


async def _amain(port: int) -> None:
    global _stats_lock
    _stats_lock = asyncio.Lock()
    _stats["started_monotonic"] = time.monotonic()

    # Long-lived pool so task_count stays high even between HTTP requests.
    for i in range(LONG_TASK_POOL):
        asyncio.create_task(_long_lived_task(i), name=f"long-pool-{i}")

    churn: asyncio.Task[None] = asyncio.create_task(_churn_loop(), name="churn-loop")

    server: asyncio.Server = await asyncio.start_server(_handle_client, HOST, port)
    sockets: list[socket.socket] = list(server.sockets or [])
    bound: str = ", ".join(str(s.getsockname()) for s in sockets)
    print(
        f"async smoke listening on {bound} py={sys.version.split()[0]} tasks={_task_count()} pool={LONG_TASK_POOL}",
        flush=True,
    )

    async with server:
        try:
            await server.serve_forever()
        finally:
            churn.cancel()
            try:
                await churn
            except asyncio.CancelledError:
                pass


def main() -> None:
    port: int = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    if os.environ.get("DD_PROFILING_ENABLED", "").lower() in ("1", "true"):
        from ddtrace.profiling import Profiler  # type: ignore[attr-defined]

        prof: Any = Profiler()
        prof.start()
        print(f"profiler started pid={os.getpid()} py={sys.version.split()[0]}", flush=True)

    try:
        asyncio.run(_amain(port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
