"""
This app exists to replicate and report on failures and degraded behavior that can arise when using ddtrace with
gunicorn
"""
import os


if os.getenv("_DD_TEST_IMPORT_AUTO"):
    import ddtrace.auto  # noqa: F401  # isort: skip

import json

from ddtrace import tracer
from ddtrace.contrib.wsgi import DDWSGIMiddleware
from ddtrace.profiling import bootstrap
import ddtrace.profiling.auto  # noqa
from tests.webclient import PingFilter


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)

SCHEDULER_SENTINEL = -1
assert bootstrap.profiler._scheduler._last_export not in (None, SCHEDULER_SENTINEL)
bootstrap.profiler._scheduler._last_export = SCHEDULER_SENTINEL


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
        payload = {
            "profiler": {"is_active": bootstrap.profiler._scheduler._last_export != SCHEDULER_SENTINEL},
        }
        data = json.dumps(payload).encode("utf-8")

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(data)))])
    return iter([data])


app = DDWSGIMiddleware(simple_app)
