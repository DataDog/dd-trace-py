#!/usr/bin/env python3
"""Multithreaded span-activation demo for validating OTel thread-context publishing.

Runs several threads that continuously start/finish nested spans, so an external
reader (e.g. ctx-sharing-demo's `context-reader`) can sample the per-thread
trace_id/span_id published by `on_context_activate` (src/native/otel_thread_ctx.rs)
via `ddtrace.internal.native._native.on_context_activate`.

Linux only. Must run with the ddtrace native extension built for Linux
(use ./scripts/ddtest).
"""

import argparse
import ctypes
import os
import sys
import threading
import time


PR_SET_PTRACER = 0x59616D61
PR_SET_PTRACER_ANY = -1


def _allow_any_ptracer() -> None:
    """Let an external reader (e.g. context-reader) ptrace this process.

    The container's Yama ptrace scope is "restricted" (1): only an ancestor can
    normally attach. This process may be started as a sibling of the reader
    (both children of a launcher script), so grant tracing rights to any process
    up front instead.
    """
    if sys.platform != "linux":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(PR_SET_PTRACER, ctypes.c_ulong(PR_SET_PTRACER_ANY & 0xFFFFFFFFFFFFFFFF), 0, 0, 0)


def _format_ids(span) -> str:
    # Same byte layout `on_context_activate` publishes (src/native/otel_thread_ctx.rs):
    # trace_id as u128 big-endian (32 hex chars), span_id as u64 big-endian (16 hex chars).
    # Formatted this way so the printed ids can be diffed directly against context-reader's
    # `trace_id=`/`span_id=` label output.
    trace_id_hex = span.trace_id.to_bytes(16, "big").hex()
    span_id_hex = span.span_id.to_bytes(8, "big").hex()
    return f"trace_id={trace_id_hex} span_id={span_id_hex}"


def _span_loop(tracer, thread_index: int, stop: threading.Event) -> None:
    tid = threading.get_native_id()
    while not stop.is_set():
        with tracer.trace(f"worker.{thread_index}") as span:
            print(f"WRITE tid={tid} {_format_ids(span)}", flush=True)
            with tracer.trace(f"worker.{thread_index}.child") as child_span:
                print(f"WRITE tid={tid} {_format_ids(child_span)}", flush=True)
                time.sleep(0.05)
        time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=4, help="number of worker threads")
    parser.add_argument("--duration", type=float, default=30.0, help="seconds to run before exiting")
    args = parser.parse_args()

    _allow_any_ptracer()

    import ddtrace

    tracer = ddtrace.tracer

    print(f"PID={os.getpid()}", flush=True)

    stop = threading.Event()
    threads = [threading.Thread(target=_span_loop, args=(tracer, i, stop), daemon=True) for i in range(args.threads)]
    for t in threads:
        t.start()

    time.sleep(args.duration)
    stop.set()
    for t in threads:
        t.join(timeout=2)


if __name__ == "__main__":
    main()
