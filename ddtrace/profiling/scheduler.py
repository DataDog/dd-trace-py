# -*- encoding: utf-8 -*-
import time
from typing import Any
from typing import Callable
from typing import Optional

import ddtrace
from ddtrace.internal import periodic
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.profiling import config
from ddtrace.trace import Tracer


LOG = get_logger(__name__)


class Scheduler(periodic.PeriodicService):
    """Schedule export of recorded data."""

    def __init__(
        self,
        before_flush: Optional[Callable[[], None]] = None,
        tracer: Optional[Tracer] = ddtrace.tracer,
        interval: float = config.upload_interval,
    ) -> None:
        super(Scheduler, self).__init__(interval=interval)
        self.before_flush: Optional[Callable[[], None]] = before_flush
        self._configured_interval: float = self.interval
        # "When did the last flush finish". Read by ServerlessScheduler to decide whether enough
        # wall time has passed to be worth flushing, and used elsewhere as an "has this scheduler
        # ever exported" probe -- hence the 0 default, which must stay 0 until a real export.
        self._last_export: int = 0  # Overridden in _start_service
        # AIDEV-NOTE: deliberately NOT the same clock as _last_export. This one is "when did the
        # window the next profile will report begin", which is a different question: it advances
        # when the profile is serialized (i.e. before the blocking upload), whereas _last_export
        # advances when the upload finishes. Collapsing them either shortens every reported
        # profile by one upload round-trip or shifts ServerlessScheduler's flush cadence.
        # Stamped here as well as in _start_service so a flush() on a never-started scheduler
        # reports a plausible window instead of one starting at the Unix epoch.
        self._window_start_ns: int = time.time_ns()
        self._tracer: Optional[Tracer] = tracer
        self._enable_code_provenance: bool = config.code_provenance

    def _start_service(self) -> None:
        """Start the scheduler."""
        LOG.debug("Starting scheduler")
        super(Scheduler, self)._start_service()
        self._last_export = time.time_ns()
        self._window_start_ns = self._last_export
        LOG.debug("Scheduler started")

    def flush(self) -> None:
        """Flush events from recorder to exporters."""
        LOG.debug("Flushing events")
        if self.before_flush is not None:
            try:
                self.before_flush()
            except Exception:
                LOG.error("Scheduler before_flush hook failed", exc_info=True)

        # Advance the window *before* uploading, not after. upload() resets the profile as soon
        # as it serializes it, so the next window's samples start accumulating there; reading the
        # clock after the blocking POST returned would charge the whole upload round-trip
        # (seconds against a slow agent) to neither window, shortening every reported profile and
        # inflating every per-second rate derived from it.
        # Residual: if upload() fails before serializing, the profile is retained for the next
        # cycle but _window_start_ns has already advanced, so that cycle under-reports its
        # window. Signalling that back would require changing upload()'s contract, which is
        # shared with the C++ path.
        start_ns = self._window_start_ns
        self._window_start_ns = time.time_ns()
        ddup.upload(self._tracer, self._enable_code_provenance, start_ns=start_ns)
        # Unchanged from before the PyO3 window accounting existed: stamped once the upload has
        # actually finished, which is the semantics ServerlessScheduler's gate expects.
        self._last_export = time.time_ns()

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
        kwargs.setdefault("interval", self.FORCED_INTERVAL)
        super(ServerlessScheduler, self).__init__(*args, **kwargs)
        self._profiled_intervals: int = 0

    def periodic(self) -> None:
        self._profiled_intervals += 1

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
