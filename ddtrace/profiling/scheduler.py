# -*- encoding: utf-8 -*-
import enum
import time
from typing import Any
from typing import Callable
from typing import Optional

import ddtrace
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.profiling import config
from ddtrace.trace import Tracer


LOG = get_logger(__name__)


# Grab the raw stdlib lock (C implementation where available), the same way
# ddtrace.internal.threads does, but without importing that module: it pulls in
# the native PeriodicThread, which is exactly the dependency the profiler's
# upload path is meant to avoid. Using the raw lock also dodges gevent
# monkey-patching of the threading module.
try:
    from _thread import allocate_lock as _Lock
except ImportError:
    from threading import Lock as _Lock


class SchedulerStatus(enum.Enum):
    STOPPED = "stopped"
    RUNNING = "running"


class Scheduler:
    """Schedule export of recorded data.

    Uploads are driven by the profiler's own native upload thread (see
    ``ddup.start_upload_thread``). The scheduler deliberately implements its own
    start/stop lifecycle instead of inheriting from ``ddtrace.internal.service``
    so that the profiler's upload path does not depend on dd-trace-py's
    threading machinery (PeriodicThread/PeriodicService).
    """

    def __init__(
        self,
        before_flush: Optional[Callable[[], None]] = None,
        tracer: Optional[Tracer] = ddtrace.tracer,
        interval: float = config.upload_interval,
    ) -> None:
        self.before_flush: Optional[Callable[[], None]] = before_flush
        self.interval: float = interval
        self._configured_interval: float = interval
        self._last_export: int = 0  # Overridden in start
        self._tracer: Optional[Tracer] = tracer
        self._enable_code_provenance: bool = config.code_provenance
        self.status: SchedulerStatus = SchedulerStatus.STOPPED
        self._lock = _Lock()

    def start(self) -> None:
        """Start the scheduler. Idempotent: a no-op if already running."""
        with self._lock:
            if self.status == SchedulerStatus.RUNNING:
                return
            LOG.debug("Starting scheduler")
            self._last_export = time.time_ns()
            ddup.start_upload_thread(self.interval, self.periodic)
            self.status = SchedulerStatus.RUNNING
            LOG.debug("Scheduler started")

    def stop(self) -> None:
        """Stop the scheduler and join its upload thread. Idempotent."""
        with self._lock:
            if self.status == SchedulerStatus.STOPPED:
                return
            LOG.debug("Stopping scheduler")
            ddup.stop_upload_thread()
            self.status = SchedulerStatus.STOPPED
            LOG.debug("Scheduler stopped")

    def join(self, timeout: Optional[float] = None) -> None:
        """Join the upload thread.

        ``stop`` already joins the native upload thread, so this is a no-op kept
        for interface compatibility with the profiler's lifecycle calls.
        """

    def flush(self) -> None:
        """Flush events from recorder to exporters."""
        LOG.debug("Flushing events")
        if self.before_flush is not None:
            try:
                self.before_flush()
            except Exception:
                LOG.error("Scheduler before_flush hook failed", exc_info=True)

        ddup.upload(self._tracer, self._enable_code_provenance)

        self._last_export = time.time_ns()

    def periodic(self) -> float:
        """Run one flush and return the number of seconds to wait before the next one."""
        start_time = time.monotonic()
        try:
            self.flush()
        finally:
            self.interval = max(0.0, self._configured_interval - (time.monotonic() - start_time))
        return self.interval


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
        kwargs.setdefault("interval", self.FORCED_INTERVAL)
        super(ServerlessScheduler, self).__init__(*args, **kwargs)
        self._profiled_intervals: int = 0

    def periodic(self) -> float:
        # Check both the number of intervals and time frame to be sure we don't flush, e.g., empty profiles
        if self._profiled_intervals >= self.FLUSH_AFTER_INTERVALS and (time.time_ns() - self._last_export) >= int(
            self.FORCED_INTERVAL * self.FLUSH_AFTER_INTERVALS * 1e9
        ):
            try:
                super(ServerlessScheduler, self).periodic()
            finally:
                # Override interval so it's always back to the value we need
                self.interval = self.FORCED_INTERVAL
                self._profiled_intervals = 0
        else:
            self._profiled_intervals += 1
        return self.FORCED_INTERVAL
