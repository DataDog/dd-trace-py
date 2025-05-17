# -*- encoding: utf-8 -*-
import logging
import time
from typing import Any  # noqa F401
from typing import Callable
from typing import Optional

import ddtrace
from ddtrace.internal import periodic
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.settings.profiling import config
from ddtrace.trace import Tracer


LOG = logging.getLogger(__name__)


class Scheduler(periodic.PeriodicService):
    """Schedule export of recorded data."""

    def __init__(
        self,
        before_flush: Optional[Callable] = None,
        tracer: Optional[Tracer] = ddtrace.tracer,
        interval: float = config.upload_interval,
    ):
        super(Scheduler, self).__init__(interval=interval)
        self.before_flush: Optional[Callable] = before_flush
        self._configured_interval: float = self.interval
        self._last_export: int = 0  # Overridden in _start_service
        self._tracer = tracer
        self._enable_code_provenance: bool = config.code_provenance

    def _start_service(self):
        # type: (...) -> None
        """Start the scheduler."""
        LOG.debug("Starting scheduler")
        super(Scheduler, self)._start_service()
        self._last_export = time.time_ns()
        LOG.debug("Scheduler started")

    def flush(self):
        # type: (...) -> None
        """Flush events to the libdatadog exporter"""
        LOG.debug("Flushing events")
        if self.before_flush is not None:
            try:
                self.before_flush()
            except Exception:
                LOG.error("Scheduler before_flush hook failed", exc_info=True)

        ddup.upload(self._tracer, self._enable_code_provenance)
        self._last_export = time.time_ns()
        return

    def periodic(self):
        # type: (...) -> None
        start_time = time.monotonic()
        try:
            self.flush()
        finally:
            self.interval = max(0, self._configured_interval - (time.monotonic() - start_time))


class ServerlessScheduler(Scheduler):
    """Serverless scheduler that works on, e.g., AWS Lambda.

    The idea with this scheduler is to not sleep 60s, but to sleep 1s and flush out profiles after 60 sleeping period.
    As the service can be frozen a few seconds after flushing out a profile, we want to make sure the next flush is not
    > 60s later, but after at least 60 periods of 1s.

    """

    # We force this interval everywhere
    FORCED_INTERVAL = 1.0
    FLUSH_AFTER_INTERVALS = 60.0

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        kwargs.setdefault("interval", self.FORCED_INTERVAL)
        super(ServerlessScheduler, self).__init__(*args, **kwargs)
        self._profiled_intervals: int = 0

    def periodic(self):
        # type: (...) -> None
        # Check both the number of intervals and time frame to be sure we don't flush, e.g., empty profiles
        if self._profiled_intervals >= self.FLUSH_AFTER_INTERVALS and (time.time_ns() - self._last_export) >= (
            self.FORCED_INTERVAL * self.FLUSH_AFTER_INTERVALS
        ):
            try:
                super(ServerlessScheduler, self).periodic()
            finally:
                # Override interval so it's always back to the value we n
                self.interval = self.FORCED_INTERVAL
                self._profiled_intervals = 0
        else:
            self._profiled_intervals += 1
