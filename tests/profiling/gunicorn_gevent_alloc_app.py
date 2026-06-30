# -*- encoding: utf-8 -*-
"""WSGI app used to demonstrate greenlet-level memory attribution.

Each request spawns several greenlets that each allocate a sizeable Python
structure and yield to the gevent hub between allocations. This mirrors the
"concurrent cache rebuild" pile-up pattern (many greenlets executing the same
allocation-heavy function within a single OS thread) that motivated greenlet
attribution for the memory profiler.

All greenlets share one OS thread, so before greenlet attribution every
allocation collapses onto a single thread-id lane. With attribution, each
greenlet's allocations carry a distinct ``task id`` label.
"""

from __future__ import annotations

import os
import threading
from typing import Callable

import gevent


NUM_GREENLETS = 16
# Each chunk allocates a list of dicts; tuned to reliably cross the memory
# profiler's allocation sampling threshold within a single request.
CHUNKS_PER_GREENLET = 6
ITEMS_PER_CHUNK = 20000


def allocate_in_greenlet(n: int) -> int:
    """Allocate object-domain memory, yielding between chunks so that
    greenlets interleave within the single worker OS thread.
    """
    total = 0
    for _ in range(CHUNKS_PER_GREENLET):
        data = [{"i": i, "n": n, "payload": "x" * 48} for i in range(ITEMS_PER_CHUNK)]
        total += len(data)
        # Yield to the hub so other greenlets get scheduled on this thread.
        gevent.sleep(0)
        del data
    return total


def app(environ: dict[str, str], start_response: Callable[[str, list[tuple[str, str]]], None]) -> list[bytes]:
    greenlets = [gevent.spawn(allocate_in_greenlet, i) for i in range(NUM_GREENLETS)]
    gevent.joinall(greenlets)
    total = sum(g.value or 0 for g in greenlets)

    response_str = (
        f"allocated across {len(greenlets)} greenlets, total items {total} "
        f"at pid {os.getpid()} tid {threading.get_ident()}"
    )
    response_body = response_str.encode("utf-8")

    status = "200 OK" if response_body else "404 Not Found"
    headers = [("Content-type", "text/plain")]
    start_response(status, headers)
    return [response_body]
