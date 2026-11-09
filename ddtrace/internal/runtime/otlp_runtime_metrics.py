"""Python runtime metrics exported over OTLP through the OpenTelemetry metrics API.

Names, units and attributes match ``opentelemetry-instrumentation-system-metrics``, which the
Datadog backend maps to the Python runtime metrics experience.
"""

from __future__ import annotations

import gc
import os
import time
from typing import Any
from typing import Iterable

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import process_metrics
from ddtrace.internal.threads import Lock
from ddtrace.version import __version__

from .metric_collectors import _ProcessMetrics


log = get_logger(__name__)

METER_NAME = "datadog.python.runtime"

_NS_TO_SEC = 1e-9

# CPU time and context switch types, as reported in the ``type`` attribute.
CPU_TIME_SYSTEM = "system"
CPU_TIME_USER = "user"
CTX_SWITCH_VOLUNTARY = "voluntary"
CTX_SWITCH_INVOLUNTARY = "involuntary"
_PROCESS_COUNTERS = (CPU_TIME_SYSTEM, CPU_TIME_USER, CTX_SWITCH_VOLUNTARY, CTX_SWITCH_INVOLUNTARY)

_enabled = False
_instruments_registered = False
_lock = Lock()


def _read_process_counters(reading: _ProcessMetrics) -> dict[str, float]:
    return {
        CPU_TIME_SYSTEM: reading.cpu_time_sys_ns * _NS_TO_SEC,
        CPU_TIME_USER: reading.cpu_time_user_ns * _NS_TO_SEC,
        CTX_SWITCH_VOLUNTARY: reading.ctx_switches_voluntary,
        CTX_SWITCH_INVOLUNTARY: reading.ctx_switches_involuntary,
    }


class _ProcessCounters:
    """Process CPU time and context switch totals that never decrease, even across fork.

    A forked child's OS counters restart near zero, so after a fork the totals keep growing by
    the child's own increments instead of jumping back to its raw readings.
    """

    def __init__(self) -> None:
        self._last_readings = _read_process_counters(_ProcessMetrics(*process_metrics()))
        self.totals = dict(self._last_readings)
        self._last_cpu_seconds = self.cpu_seconds()
        self._last_wall_time = time.monotonic()
        forksafe.register(self._reset_baseline)

    def _reset_baseline(self) -> None:
        self._last_readings = _read_process_counters(_ProcessMetrics(*process_metrics()))

    def update(self) -> _ProcessMetrics:
        reading = _ProcessMetrics(*process_metrics())
        for name, value in _read_process_counters(reading).items():
            # A negative reading means the platform cannot report this counter.
            if value >= 0:
                self.totals[name] += max(0, value - self._last_readings[name])
            self._last_readings[name] = value
        return reading

    def cpu_seconds(self) -> float:
        return self.totals[CPU_TIME_SYSTEM] + self.totals[CPU_TIME_USER]

    def cpu_utilization(self, cpu_count: int) -> float:
        now = time.monotonic()
        elapsed = now - self._last_wall_time
        cpu_seconds = self.cpu_seconds()
        used_cpu_seconds = cpu_seconds - self._last_cpu_seconds
        self._last_wall_time = now
        self._last_cpu_seconds = cpu_seconds
        if elapsed <= 0:
            return 0.0
        return used_cpu_seconds / elapsed / cpu_count


def _get_meter_provider() -> Any:
    """Return the MeterProvider to register on, or None when no working provider is available."""
    # Importing opentelemetry first lets the bootstrap's opentelemetry import hook run before, not
    # during, the import of ddtrace.internal.opentelemetry.metrics, which that hook also imports.
    import opentelemetry  # noqa: F401

    from ddtrace.internal.opentelemetry.metrics import ensure_otel_meter_provider

    if ensure_otel_meter_provider():
        from opentelemetry.metrics import get_meter_provider

        return get_meter_provider()

    try:
        from opentelemetry.metrics._internal import _METER_PROVIDER
    except ImportError:
        return None
    # Use a MeterProvider the application installed before ddtrace could set up its own.
    return _METER_PROVIDER


def _register_instruments(meter_provider: Any) -> None:
    from opentelemetry.metrics import Observation

    meter = meter_provider.get_meter(METER_NAME, __version__)
    counters = _ProcessCounters()
    cpu_count = os.cpu_count() or 1

    def observe_cpu_time(_options: Any) -> Iterable[Observation]:
        if not _enabled:
            return []
        counters.update()
        return [Observation(counters.totals[t], {"type": t}) for t in (CPU_TIME_SYSTEM, CPU_TIME_USER)]

    def observe_cpu_utilization(_options: Any) -> Iterable[Observation]:
        if not _enabled:
            return []
        counters.update()
        return [Observation(counters.cpu_utilization(cpu_count))]

    def observe_context_switches(_options: Any) -> Iterable[Observation]:
        if not _enabled:
            return []
        reading = counters.update()
        supported = {
            CTX_SWITCH_VOLUNTARY: reading.ctx_switches_voluntary >= 0,
            CTX_SWITCH_INVOLUNTARY: reading.ctx_switches_involuntary >= 0,
        }
        return [Observation(counters.totals[t], {"type": t}) for t, is_supported in supported.items() if is_supported]

    def observe_memory_usage(_options: Any) -> Iterable[Observation]:
        if not _enabled:
            return []
        return [Observation(_ProcessMetrics(*process_metrics()).rss_bytes)]

    def observe_thread_count(_options: Any) -> Iterable[Observation]:
        if not _enabled:
            return []
        return [Observation(_ProcessMetrics(*process_metrics()).num_threads)]

    def observe_gc_stat(stat: str) -> Any:
        def observe(_options: Any) -> Iterable[Observation]:
            if not _enabled:
                return []
            return [
                Observation(gen_stats[stat], {"cpython.gc.generation": generation})
                for generation, gen_stats in enumerate(gc.get_stats())
            ]

        return observe

    def observe_gc_count(_options: Any) -> Iterable[Observation]:
        if not _enabled:
            return []
        return [Observation(count, {"count": str(generation)}) for generation, count in enumerate(gc.get_count())]

    meter.create_observable_counter(
        "process.cpu.time",
        callbacks=[observe_cpu_time],
        unit="s",
        description="Total CPU seconds broken down by different states.",
    )
    meter.create_observable_gauge(
        "process.cpu.utilization",
        callbacks=[observe_cpu_utilization],
        unit="1",
        description="Difference in process.cpu.time since the last measurement, divided by the elapsed time "
        "and number of CPUs available to the process.",
    )
    meter.create_observable_counter(
        "process.context_switches",
        callbacks=[observe_context_switches],
        unit="{context_switch}",
        description="Number of times the process has been context switched.",
    )
    meter.create_observable_up_down_counter(
        "process.memory.usage",
        callbacks=[observe_memory_usage],
        unit="By",
        description="The amount of physical memory in use.",
    )
    meter.create_observable_up_down_counter(
        "process.thread.count",
        callbacks=[observe_thread_count],
        unit="{thread}",
        description="Process threads count.",
    )
    meter.create_observable_counter(
        "cpython.gc.collections",
        callbacks=[observe_gc_stat("collections")],
        unit="{collection}",
        description="The number of times a generation was collected since interpreter start.",
    )
    meter.create_observable_counter(
        "cpython.gc.collected_objects",
        callbacks=[observe_gc_stat("collected")],
        unit="{object}",
        description="The total number of objects collected since interpreter start.",
    )
    meter.create_observable_counter(
        "cpython.gc.uncollectable_objects",
        callbacks=[observe_gc_stat("uncollectable")],
        unit="{object}",
        description="The total number of uncollectable objects found since interpreter start.",
    )
    # Deprecated upstream, but it is the name the Datadog backend maps to runtime.python.gc.count.*.
    meter.create_observable_counter(
        "process.runtime.cpython.gc_count",
        callbacks=[observe_gc_count],
        unit="{object}",
        description="Runtime cpython GC count",
    )
    # GC pause time and max have no OpenTelemetry semantic convention, so they are not exported here.
    # Upstreaming a CPython GC pause metric to the semantic conventions would close that gap.


def start() -> bool:
    """Start exporting runtime metrics over OTLP and return False when no MeterProvider is available."""
    global _enabled, _instruments_registered
    with _lock:
        if not _instruments_registered:
            meter_provider = _get_meter_provider()
            if meter_provider is None:
                return False
            try:
                _register_instruments(meter_provider)
            except Exception:
                log.warning("Failed to register OpenTelemetry runtime metrics instruments", exc_info=True)
                return False
            _instruments_registered = True
        _enabled = True
        return True


def stop() -> None:
    """Stop reporting runtime metrics; the instruments stay registered but observe nothing."""
    global _enabled
    with _lock:
        _enabled = False
