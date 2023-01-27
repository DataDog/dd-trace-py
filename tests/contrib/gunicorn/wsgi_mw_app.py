"""
This app exists to replicate and report on failures and degraded behavior that can arise when using ddtrace with
gunicorn
"""
import json
import os
import sys

from ddtrace import tracer
from ddtrace.contrib.wsgi import DDWSGIMiddleware
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

if sys.version_info < (3, 11):
    from ddtrace.debugging import DynamicInstrumentation


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
        data = bytes("goodbye", encoding="utf-8")
    else:
        has_config_worker = hasattr(RemoteConfig._worker, "_worker")
        payload = {
            "remoteconfig": {
                "worker_alive": has_config_worker and RemoteConfig._worker._worker.is_alive(),
                "enabled_after_gevent_monkeypatch": RemoteConfig._was_enabled_after_gevent_monkeypatch,
            },
        }
        json_payload = json.dumps(payload)
        data = bytes(json_payload, encoding="utf-8")

    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(data)))])
    return iter([data])


app = DDWSGIMiddleware(simple_app)
