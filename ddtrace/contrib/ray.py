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
        log.warning("Deactivating OpenTelemetry tracing in favor of Datadog tracing as both are not compatible.")
        tracing_helper._global_is_tracing_enabled = False
