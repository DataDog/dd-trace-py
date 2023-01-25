"""
This app exists to replicate and report on failures and degraded behavior that can arise when using ddtrace with
gunicorn
"""
import os


if os.getenv("_DD_TEST_IMPORT_SITECUSTOMIZE"):
    import ddtrace.bootstrap.sitecustomize  # noqa: F401  # isort: skip

import json

from ddtrace import tracer
from ddtrace.contrib.wsgi import DDWSGIMiddleware
from ddtrace.debugging import DynamicInstrumentation
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.profiling.collector.memalloc import MemoryCollector
from ddtrace.profiling.profiler import Profiler
from tests.webclient import PingFilter


# starting the profiler here allows test cases to exercise the code paths that
# restart threads preexisting a forked process
Profiler._profiler = Profiler()
Profiler._profiler.start()


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)


def aggressive_shutdown():
    tracer.shutdown(timeout=1)
    RemoteConfig.disable()
    DynamicInstrumentation.disable()
    Profiler._profiler._scheduler.stop()
    Profiler._profiler.stop()


def simple_app(environ, start_response):
    if environ["RAW_URI"] == "/shutdown":
        aggressive_shutdown()
        data = bytes("goodbye", encoding="utf-8")
    else:
        has_config_worker = hasattr(RemoteConfig._worker, "_worker")
        memory_collector = [c for c in Profiler._profiler._collectors if isinstance(c, MemoryCollector)][0]
        payload = {
            "remoteconfig": {
                "worker_alive": has_config_worker and RemoteConfig._worker._worker.is_alive(),
                "enabled_after_gevent_monkeypatch": RemoteConfig._was_enabled_after_gevent_monkeypatch,
            },
            "profiler": {
                "worker_alive": memory_collector._worker.is_alive(),
                "enabled_after_gevent_monkeypatch": Profiler._was_enabled_after_gevent_monkeypatch,
            },
        }
        data = bytes(json.dumps(payload), encoding="utf-8")

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(data)))])
    return iter([data])


app = DDWSGIMiddleware(simple_app)
