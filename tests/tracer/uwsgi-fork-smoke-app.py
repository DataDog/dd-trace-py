"""WSGI app used by test_uwsgi_fork_hooks.py.

Under --py-call-uwsgi-fork-hooks, uwsgi drives CPython's os.register_at_fork
machinery around every worker fork, so ddtrace's general fork-safety registry
(ddtrace.internal.forksafe) should reinitialize itself in each worker without
any uwsgi-specific postfork bridging. This module is imported once, before any
worker is forked (uwsgi loads the app once in non-lazy mode), then registers a
callback that runs synchronously inside the child right after the fork -- the
same place ddtrace's own forksafe registry runs -- and there creates and
finishes a span to prove the tracer still works post-fork.
"""

import os
from typing import Any
from typing import Callable
from typing import Iterable

from ddtrace import tracer
from ddtrace.internal import runtime


_output_dir = os.environ["DD_TEST_FORK_SMOKE_OUTPUT_DIR"]


def _write(line: str) -> None:
    path = os.path.join(_output_dir, "worker-%d.log" % os.getpid())
    with open(path, "a") as f:
        f.write(line + "\n")


def _on_fork(new_runtime_id: str) -> None:
    try:
        with tracer.trace("smoke.post_fork") as span:
            ok = bool(span.trace_id) and bool(span.span_id)
    except Exception as e:  # noqa: BLE001
        _write("error %s %r" % (new_runtime_id, e))
        return
    _write("changed %s %s" % (new_runtime_id, ok))


runtime.on_runtime_id_change(_on_fork)


def application(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]
