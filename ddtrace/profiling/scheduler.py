import time
from typing import Any
from typing import Callable
from typing import Optional

import ddtrace
from ddtrace.internal import periodic
from ddtrace.internal import service
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.datadog.profiling._types import ProfilerTracer
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


LOG = get_logger(__name__)


def _report_frame_stats(frame_stats: dict[str, Any]) -> None:
    """Send per-collector stack truncation counts for one uploaded profile to telemetry."""
    collectors: dict[str, dict[str, Any]] = frame_stats["collectors"]
    for collector, stats in collectors.items():
        tags: tuple[tuple[str, str], ...] = (("collector", collector), ("max_frames", str(stats["max_frames"])))
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.PROFILER, "frames.samples", stats["samples"], tags)
        telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.PROFILER, "frames.samples_truncated", stats["samples_truncated"], tags
        )
        telemetry_writer.add_distribution_metric(
            TELEMETRY_NAMESPACE.PROFILER, "frames.max_depth", stats["max_depth"], tags
        )
        depth_buckets: dict[str, int] = stats["depth_buckets"]
        for bucket, count in depth_buckets.items():
            if count:
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.PROFILER, "frames.stack_depth", count, tags + (("depth", bucket),)
                )

    walk_ns: int = frame_stats["memalloc_walk_ns"]
    if walk_ns and "alloc" in collectors:
        memalloc_tags: tuple[tuple[str, str], ...] = (("max_frames", str(collectors["alloc"]["max_frames"])),)
        telemetry_writer.add_distribution_metric(
            TELEMETRY_NAMESPACE.PROFILER, "memalloc.walk_ns", walk_ns, memalloc_tags
        )


class Scheduler(periodic.PeriodicService):
    """Schedule export of recorded data."""

    def __init__(
        self,
        before_flush: Optional[Callable[[], None]] = None,
        tracer: Optional[ProfilerTracer] = ddtrace.tracer,
        interval: Optional[float] = None,
    ) -> None:
        super().__init__(interval=interval if interval is not None else config.upload_interval)
        self.before_flush: Optional[Callable[[], None]] = before_flush
        self._configured_interval: float = self.interval
        self._last_export: int = 0  # Overridden in _start_service
        self._tracer: Optional[ProfilerTracer] = tracer
        self._enable_code_provenance: bool = config.code_provenance

    def _start_service(self) -> None:
        """Start the scheduler."""
        LOG.debug("[profiling] Starting scheduler")
        ddup.reset(self._tracer)
        super()._start_service()
        self._last_export = time.time_ns()
        LOG.debug("[profiling] Scheduler started")

    def _rollback_start(self) -> None:
        with self._service_lock:
            self._stop_service()
            self.status = service.ServiceStatus.STOPPED

    def flush(self) -> None:
        """Flush profiles to exporters."""
        LOG.debug("[profiling] Flushing profiles")
        if self.before_flush is not None:
            try:
                self.before_flush()
            except Exception:
                LOG.error("[profiling] Scheduler before_flush hook failed", exc_info=True)

        try:
            ddup.upload(self._tracer, self._enable_code_provenance)
        except Exception:
            LOG.error("[profiling] Failed to upload profile", exc_info=True)
        finally:
            self._last_export = time.time_ns()

        try:
            _report_frame_stats(ddup.take_frame_stats())
        except Exception:
            LOG.debug("[profiling] Failed to report frame stats", exc_info=True)

    def periodic(self) -> None:
        start_time = time.monotonic()
        try:
            self.flush()
        finally:
            self.interval = max(0, self._configured_interval - (time.monotonic() - start_time))


class ServerlessScheduler(Scheduler):
    """Serverless scheduler that works on, e.g., AWS Lambda.

    The idea with this scheduler is to not sleep 60s, but to sleep 1s and flush out profiles after 60 sleeping period.
    As the service can be frozen a few seconds after flushing out a profile, we want to make sure the next flush is not
    > 60s later, but after at least 60 periods of 1s.

    """

    # We force this interval everywhere
    FORCED_INTERVAL = 1.0
    FLUSH_AFTER_INTERVALS = 60.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("interval") is None:
            kwargs["interval"] = self.FORCED_INTERVAL

        super().__init__(*args, **kwargs)
        self._profiled_intervals: int = 0

    def periodic(self) -> None:
        self._profiled_intervals += 1

        # Check both the number of intervals and time frame to be sure we don't flush, e.g., empty profiles
        if self._profiled_intervals >= self.FLUSH_AFTER_INTERVALS and (time.time_ns() - self._last_export) >= int(
            self.FORCED_INTERVAL * self.FLUSH_AFTER_INTERVALS * 1e9
        ):
            try:
                super().periodic()
            finally:
                # Override interval so it's always back to the value we need
                self.interval = self.FORCED_INTERVAL
                self._profiled_intervals = 0
