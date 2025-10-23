"""
This app exists to replicate and report on failures and degraded behavior that can arise when using ddtrace with
gunicorn
"""

import os


if os.getenv("_DD_TEST_IMPORT_AUTO"):
    import ddtrace.auto  # noqa: F401  # isort: skip

import json

from ddtrace.contrib.internal.wsgi.wsgi import DDWSGIMiddleware
from ddtrace.trace import tracer
from tests.webclient import PingFilter


tracer.configure(trace_processors=[PingFilter()])


def aggressive_shutdown():
    tracer.shutdown(timeout=1)


def simple_app(environ, start_response):
    if environ["RAW_URI"] == "/shutdown":
        aggressive_shutdown()
        data = b"goodbye"
    else:
        from ddtrace.profiling.profiler import Profiler

        payload = {
            "profiler": {
                # Once the scheduler is initialized, the last_export is set to a
                # timestamp using time.time_ns()
                "is_active": Profiler._instance._scheduler._last_export > 0
                if Profiler._instance is not None
                else False,
            },
        }
        data = json.dumps(payload).encode("utf-8")

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(data)))])
    return iter([data])


app = DDWSGIMiddleware(simple_app)
