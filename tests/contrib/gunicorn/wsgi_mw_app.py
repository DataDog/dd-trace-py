"""
This app exists to replicate and report on failures and degraded behavior that can arise when using ddtrace with
gunicorn
"""
import json
import os
import sys

from ddtrace import tracer
from ddtrace.contrib.wsgi import DDWSGIMiddleware
from ddtrace.debugging import DynamicInstrumentation
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.profiling import bootstrap
import ddtrace.profiling.auto  # noqa
from tests.webclient import PingFilter


if os.getenv("_DD_TEST_IMPORT_SITECUSTOMIZE"):
    import ddtrace.bootstrap.sitecustomize  # noqa: F401  # isort: skip

tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)


def aggressive_shutdown():
    RemoteConfig.disable()
    if sys.version_info < (3, 11):
        DynamicInstrumentation.disable()
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
            "remoteconfig": {
                "worker_alive": hasattr(RemoteConfig._worker, "_worker") and RemoteConfig._worker._worker.is_alive(),
            },
        }
        data = json.dumps(payload).encode("utf-8")

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(data)))])
    return iter([data])


app = DDWSGIMiddleware(simple_app)
