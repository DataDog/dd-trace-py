import logging
import os

from ray.util.tracing import tracing_helper
from ray.util.tracing.tracing_helper import _is_tracing_enabled

from ddtrace import patch
import ddtrace.auto  # noqa:F401


log = logging.getLogger(__name__)


def setup_tracing():
    os.environ["DD_TRACE_REPORT_HOSTNAME"] = "True"
    patch(ray=True)

    if _is_tracing_enabled():
        # Specifying a hook will activate opentelemetry instrumentation automatically.
        # We silently deactivate it as users might be confused as they
        # probably do not activate otel on purpose
        tracing_helper._global_is_tracing_enabled = False
