"""Celery app whose embedded beat checks that its sockets survive native runtime activity.

The fork hook below makes the first native call in the beat child before Celery closes the
inherited file descriptors, so the native runtime is rebuilt right before the sweep.
"""

import os
import socket
import sys

from celery import Celery
from celery import signals


app = Celery("beat_fds")


def _native_call():
    from ddtrace.internal.http_client import HTTPClient

    try:
        HTTPClient(os.environ.get("DD_TRACE_AGENT_URL", "http://127.0.0.1:8126"), timeout_ms=500).get("/info")
    except Exception:
        pass


os.register_at_fork(after_in_child=_native_call)


@signals.beat_init.connect
def _check_sockets(sender=None, **kwargs):
    # Opened after the sweep, these sockets reuse the descriptor numbers it freed.
    pairs = [socket.socketpair() for _ in range(16)]
    _native_call()
    from ddtrace.internal.native_runtime import get_native_runtime

    get_native_runtime().shutdown(timeout_ms=3000)
    for a, b in pairs:
        b.setblocking(False)
        try:
            data = b.recv(64)
        except BlockingIOError:
            data = None
        a.sendall(b"ping")
        b.settimeout(2)
        if data is not None or b.recv(64) != b"ping":
            print("BEAT_FDS_BAD", data, file=sys.stderr, flush=True)
            return
    print("BEAT_FDS_OK", file=sys.stderr, flush=True)
