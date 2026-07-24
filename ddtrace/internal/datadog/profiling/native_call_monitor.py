"""
Native C function call tracking using sys.monitoring (Python 3.12+).

Uses a C-level sys.monitoring CALL handler that registers each call site
in a static registry keyed by (code_object, lasti), then returns DISABLE
for every callable (Python and C). After warmup (one fire per call site),
overhead drops to zero. The sampler looks up (code_object, lasti) when
reading frames to inject native call information.
"""

import sys

from ddtrace.internal.logger import get_logger
from ddtrace.internal.monitoring import monitoring_registry


log = get_logger(__name__)

_TOOL_NAME = "dd-profiling"

# Protected by the GIL -- start() and stop() are only called from Python code
# holding the GIL, so concurrent access is not possible.
_started = False


def start() -> None:
    """Start sys.monitoring-based native call tracking from C."""
    global _started
    if _started:
        return

    if sys.version_info < (3, 12):
        return

    from ddtrace.internal.datadog.profiling.stack._stack import start_native_monitoring

    # uses_disable=True: the C CALL handler returns sys.monitoring.DISABLE for every call site
    # so each fires only once. A global sys.monitoring.restart_events() (e.g. from the coverage
    # collector) would re-arm them and destroy the zero-overhead-after-warmup behaviour, so the
    # registry must advertise this tool as restart-sensitive.
    tool_id = monitoring_registry.acquire(_TOOL_NAME, uses_disable=True)
    if tool_id is None:
        log.error("Failed to start native monitoring in the profiler: no sys.monitoring tool slot is available. ")
        return

    # The registry already claimed the slot with use_tool_id; hand the id to the
    # C++ extension so it only wires up the CALL event and callback.
    try:
        start_native_monitoring(tool_id)
    except Exception:
        monitoring_registry.release(_TOOL_NAME)
        log.error("Failed to start native monitoring in the profiler", exc_info=True)
        return

    _started = True


def stop():
    """Stop sys.monitoring-based native call tracking."""
    global _started
    if not _started:
        return

    from ddtrace.internal.datadog.profiling.stack._stack import stop_native_monitoring

    try:
        stop_native_monitoring()
    finally:
        # The registry owns the slot lifetime (it called use_tool_id), so it is
        # also responsible for freeing it.
        monitoring_registry.release(_TOOL_NAME)
        _started = False
