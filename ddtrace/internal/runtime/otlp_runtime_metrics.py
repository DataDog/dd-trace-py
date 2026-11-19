"""Runtime metrics reported through the OpenTelemetry metrics API.

When runtime metrics and OpenTelemetry metrics are both enabled, the runtime metrics are reported under their
OpenTelemetry semantic convention names, through the meter provider that ddtrace configures to export them over OTLP,
instead of being sent to DogStatsD:

- https://opentelemetry.io/docs/specs/semconv/runtime/cpython-metrics/
- https://opentelemetry.io/docs/specs/semconv/system/process-metrics/

Like the meter provider, this module is only loaded once the application imports OpenTelemetry.
"""

import gc
from typing import Callable

from opentelemetry.metrics import CallbackOptions
from opentelemetry.metrics import Meter
from opentelemetry.metrics import Observation
from opentelemetry.metrics import get_meter

from .. import forksafe
from ..logger import get_logger
from .metric_collectors import _ProcessMetrics


log = get_logger(__name__)

METER_NAME = "datadog.runtime_metrics"

_NS_TO_SEC = 1e-9

# The OpenTelemetry metrics API cannot unregister a callback, so the instruments are created once, and their
# callbacks report nothing while the runtime metrics are disabled.
_enabled = False
_instruments_created = False

# The OpenTelemetry SDK computes the deltas of the observable counters from the last values that they reported, which a
# forked child inherits from its parent, while the CPU time and context switches of the child restart from zero. So that
# these counters stay monotonic, the child reports them on top of the last values that its parent reported.
_last_reported: dict[str, float] = {}
_fork_offsets: dict[str, float] = {}


def _continue_counters_after_fork() -> None:
    _fork_offsets.update(_last_reported)


def _monotonic(counter: str, value: float) -> float:
    reported = _fork_offsets.get(counter, 0) + value
    _last_reported[counter] = reported
    return reported


def start() -> None:
    """Start reporting the runtime metrics through the OpenTelemetry metrics API."""
    global _enabled, _instruments_created

    if not _instruments_created:
        _create_instruments(get_meter(METER_NAME))
        _instruments_created = True

    _enabled = True
    log.debug("Runtime metrics are reported through the OpenTelemetry metrics API")


def stop() -> None:
    """Stop reporting the runtime metrics."""
    global _enabled

    _enabled = False


def _create_instruments(meter: Meter) -> None:
    meter.create_observable_counter(
        "cpython.gc.collections",
        callbacks=[_gc_callback("collections")],
        unit="{collection}",
        description="The number of times a generation was collected since interpreter start.",
    )
    meter.create_observable_counter(
        "cpython.gc.collected_objects",
        callbacks=[_gc_callback("collected")],
        unit="{object}",
        description="The total number of objects collected inside a generation since interpreter start.",
    )
    meter.create_observable_counter(
        "cpython.gc.uncollectable_objects",
        callbacks=[_gc_callback("uncollectable")],
        unit="{object}",
        description="The total number of objects which were found to be uncollectable inside a generation since "
        "interpreter start.",
    )

    try:
        _process_metrics()
    except Exception:
        log.debug("Process metrics are not available on this platform", exc_info=True)
        return

    forksafe.register(_continue_counters_after_fork)

    meter.create_observable_counter(
        "process.cpu.time",
        callbacks=[_observe_cpu_time],
        unit="s",
        description="Total CPU seconds broken down by different CPU modes.",
    )
    meter.create_observable_up_down_counter(
        "process.memory.usage",
        callbacks=[_observe_memory_usage],
        unit="By",
        description="The amount of physical memory in use.",
    )
    meter.create_observable_up_down_counter(
        "process.thread.count",
        callbacks=[_observe_thread_count],
        unit="{thread}",
        description="Process threads count.",
    )
    meter.create_observable_counter(
        "process.context_switches",
        callbacks=[_observe_context_switches],
        unit="{context_switch}",
        description="Number of times the process has been context switched.",
    )


def _gc_callback(stat: str) -> Callable[[CallbackOptions], list[Observation]]:
    def callback(_options: CallbackOptions) -> list[Observation]:
        if not _enabled:
            return []
        return [
            Observation(row.get(stat, 0), {"cpython.gc.generation": generation})
            for generation, row in enumerate(gc.get_stats())
        ]

    return callback


def _process_metrics() -> _ProcessMetrics:
    from ddtrace.internal import native

    return _ProcessMetrics(*native.process_metrics())


def _observe_cpu_time(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    metrics = _process_metrics()
    return [
        Observation(_monotonic("cpu.user", metrics.cpu_time_user_ns * _NS_TO_SEC), {"cpu.mode": "user"}),
        Observation(_monotonic("cpu.system", metrics.cpu_time_sys_ns * _NS_TO_SEC), {"cpu.mode": "system"}),
    ]


def _observe_memory_usage(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    return [Observation(_process_metrics().rss_bytes)]


def _observe_thread_count(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    return [Observation(_process_metrics().num_threads)]


def _observe_context_switches(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    metrics = _process_metrics()
    # A negative count means that the platform cannot report this type of context switch.
    return [
        Observation(_monotonic(f"context_switches.{switch_type}", count), {"process.context_switch.type": switch_type})
        for switch_type, count in (
            ("voluntary", metrics.ctx_switches_voluntary),
            ("involuntary", metrics.ctx_switches_involuntary),
        )
        if count >= 0
    ]
