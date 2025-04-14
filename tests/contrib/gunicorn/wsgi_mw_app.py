"""
This app exists to replicate and report on failures and degraded behavior that can arise when using ddtrace with
gunicorn
"""

import os
import time

if os.getenv("_DD_TEST_IMPORT_AUTO"):
    import ddtrace.auto  # noqa: F401  # isort: skip

import json

from ddtrace.contrib.internal.wsgi.wsgi import DDWSGIMiddleware
from ddtrace.profiling import bootstrap
import ddtrace.profiling.auto  # noqa:F401
from ddtrace.trace import tracer
from tests.webclient import PingFilter


tracer.configure(trace_processors=[PingFilter()])


def aggressive_shutdown():
    tracer.shutdown(timeout=1)
    if hasattr(bootstrap, "profiler"):
        bootstrap.profiler._scheduler.stop()
        bootstrap.profiler.stop()


def simple_app(environ, start_response):
    if environ["RAW_URI"] == "/shutdown":
        aggressive_shutdown()
        data = b"goodbye"
    else:
        print(f"{os.getpid()} {time.monotonic_ns()} {bootstrap.profiler._scheduler._last_export} app")
        payload = {
            "profiler": {
                # Once the scheduler is initialized, the last_export is set to a
                # timestamp using time.time_ns()
                "is_active": bootstrap.profiler._scheduler._last_export
                > 0,
            },
        }
        data = json.dumps(payload).encode("utf-8")

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(data)))])
    return iter([data])


app = DDWSGIMiddleware(simple_app)
