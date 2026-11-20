"""Runtime metrics reported through the OpenTelemetry metrics API.

When runtime metrics and OpenTelemetry metrics are both enabled, the runtime metrics are reported under their
OpenTelemetry semantic convention names, through the meter provider that ddtrace configures to export them over OTLP,
instead of being sent to DogStatsD:

- https://opentelemetry.io/docs/specs/semconv/runtime/cpython-metrics/
- https://opentelemetry.io/docs/specs/semconv/system/process-metrics/

Each process reports them under its own runtime ID, in the ``datadog.runtime_id`` attribute.

Like the meter provider, this module is only loaded once the application imports OpenTelemetry.
"""

import gc
import os
from time import monotonic
from typing import Callable
from typing import Optional

from opentelemetry.metrics import CallbackOptions
from opentelemetry.metrics import Meter
from opentelemetry.metrics import Observation
from opentelemetry.metrics import get_meter

from ddtrace.internal.runtime import get_runtime_id

from .. import forksafe
from ..logger import get_logger
from . import gc_at_fork
from .metric_collectors import _ProcessMetrics


log = get_logger(__name__)

METER_NAME = "datadog.runtime_metrics"

_NS_TO_SEC = 1e-9

# The OpenTelemetry metrics API cannot unregister a callback, so the instruments are created once, and their
# callbacks report nothing while the runtime metrics are disabled.
_enabled = False
_instruments_created = False

# ddtrace gives a forked child its own runtime ID. As the OpenTelemetry SDK keeps the state of the observable counters
# per attribute set, the counters of a child then start from zero, with any temporality and reader, instead of
# continuing those of its parent.
RUNTIME_ID_ATTRIBUTE = "datadog.runtime_id"

# The time and the CPU time of the last measurement of the CPU utilization.
_cpu_sample: Optional[tuple[float, float]] = None


def _reset_after_fork() -> None:
    global _cpu_sample

    # The next collection takes the first CPU sample of the child: fork hooks do no I/O.
    _cpu_sample = None


def _reset_cpu_sample() -> None:
    global _cpu_sample

    try:
        metrics = _process_metrics()
    except Exception:
        _cpu_sample = None
        return
    _cpu_sample = (monotonic(), (metrics.cpu_time_user_ns + metrics.cpu_time_sys_ns) * _NS_TO_SEC)


def _available_cpus() -> int:
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def start() -> None:
    """Start reporting the runtime metrics through the OpenTelemetry metrics API."""
    global _enabled, _instruments_created

    gc_at_fork.record()
    if not _instruments_created:
        _create_instruments(get_meter(METER_NAME))
        _instruments_created = True

    _reset_cpu_sample()
    _enabled = True
    log.debug("Runtime metrics are reported through the OpenTelemetry metrics API")


def stop() -> None:
    """Stop reporting the runtime metrics."""
    global _enabled

    _enabled = False


def _create_instruments(meter: Meter) -> None:
    forksafe.register(_reset_after_fork)

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
    # The name and attribute of the OpenTelemetry system metrics instrumentation. As the counts drop after each
    # collection, this is a gauge.
    meter.create_observable_gauge(
        "process.runtime.cpython.gc_count",
        callbacks=[_observe_gc_count],
        unit="{count}",
        description="The current collection counts of the garbage collector, by generation.",
    )

    try:
        _process_metrics()
    except Exception:
        log.debug("Process metrics are not available on this platform", exc_info=True)
        return

    meter.create_observable_counter(
        "process.cpu.time",
        callbacks=[_observe_cpu_time],
        unit="s",
        description="Total CPU seconds broken down by different CPU modes.",
    )
    meter.create_observable_gauge(
        "process.cpu.utilization",
        callbacks=[_observe_cpu_utilization],
        unit="1",
        description="Difference in process.cpu.time since the last measurement, divided by the elapsed time and number "
        "of CPUs available to the process.",
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
        runtime_id = get_runtime_id()
        # A forked child reports the GC statistics since the fork.
        at_fork = gc_at_fork.stats
        return [
            Observation(
                row.get(stat, 0) - (at_fork[generation].get(stat, 0) if generation < len(at_fork) else 0),
                {"cpython.gc.generation": generation, RUNTIME_ID_ATTRIBUTE: runtime_id},
            )
            for generation, row in enumerate(gc.get_stats())
        ]

    return callback


def _observe_gc_count(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    runtime_id = get_runtime_id()
    return [
        Observation(count, {"count": str(generation), RUNTIME_ID_ATTRIBUTE: runtime_id})
        for generation, count in enumerate(gc.get_count())
    ]


def _process_metrics() -> _ProcessMetrics:
    from ddtrace.internal import native

    return _ProcessMetrics(*native.process_metrics())


def _observe_cpu_time(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    metrics = _process_metrics()
    runtime_id = get_runtime_id()
    return [
        Observation(metrics.cpu_time_user_ns * _NS_TO_SEC, {"cpu.mode": "user", RUNTIME_ID_ATTRIBUTE: runtime_id}),
        Observation(metrics.cpu_time_sys_ns * _NS_TO_SEC, {"cpu.mode": "system", RUNTIME_ID_ATTRIBUTE: runtime_id}),
    ]


def _observe_cpu_utilization(_options: CallbackOptions) -> list[Observation]:
    global _cpu_sample

    if not _enabled:
        return []
    metrics = _process_metrics()
    now = monotonic()
    cpu_time = (metrics.cpu_time_user_ns + metrics.cpu_time_sys_ns) * _NS_TO_SEC
    last_sample, _cpu_sample = _cpu_sample, (now, cpu_time)
    if last_sample is None or now <= last_sample[0]:
        return []
    last_time, last_cpu_time = last_sample
    utilization = (cpu_time - last_cpu_time) / (now - last_time) / _available_cpus()
    return [Observation(utilization, {RUNTIME_ID_ATTRIBUTE: get_runtime_id()})]


def _observe_memory_usage(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    return [Observation(_process_metrics().rss_bytes, {RUNTIME_ID_ATTRIBUTE: get_runtime_id()})]


def _observe_thread_count(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    return [Observation(_process_metrics().num_threads, {RUNTIME_ID_ATTRIBUTE: get_runtime_id()})]


def _observe_context_switches(_options: CallbackOptions) -> list[Observation]:
    if not _enabled:
        return []
    metrics = _process_metrics()
    runtime_id = get_runtime_id()
    # A negative count means that the platform cannot report this type of context switch.
    return [
        Observation(count, {"process.context_switch.type": switch_type, RUNTIME_ID_ATTRIBUTE: runtime_id})
        for switch_type, count in (
            ("voluntary", metrics.ctx_switches_voluntary),
            ("involuntary", metrics.ctx_switches_involuntary),
        )
        if count >= 0
    ]
