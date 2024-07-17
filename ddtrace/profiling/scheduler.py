# -*- encoding: utf-8 -*-
import logging
from typing import Callable
from typing import List
from typing import Optional

from ddtrace.internal import compat
from ddtrace.internal import periodic
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _traceback
from ddtrace.profiling import exporter
from ddtrace.settings.profiling import config

from .exporter import Exporter
from .recorder import Recorder


LOG = logging.getLogger(__name__)


class Scheduler(periodic.PeriodicService):
    """Schedule export of recorded data."""

    def __init__(
        self,
        recorder: Optional[Recorder] = None,
        exporters: Optional[List[Exporter]] = None,
        before_flush: Optional[Callable] = None,
        interval: float = config.upload_interval,
    ):
        super(Scheduler, self).__init__(interval=interval)
        self.recorder: Optional[Recorder] = recorder
        self.exporters: Optional[List[Exporter]] = exporters
        self.before_flush: Optional[Callable] = before_flush
        self._configured_interval: Optional[float] = self.interval
        self._last_export: Optional[int] = None
        self._export_libdd_enabled: bool = config.export.libdd_enabled

    def _start_service(self):
        # type: (...) -> None
        """Start the scheduler."""
        LOG.debug("Starting scheduler")
        super(Scheduler, self)._start_service()
        self._last_export = compat.time_ns()
        LOG.debug("Scheduler started")

    def flush(self):
        """Flush events from recorder to exporters."""
        LOG.debug("Flushing events")
        if self._export_libdd_enabled:
            ddup.upload()

            # These are only used by the Python uploader, but set them here to keep logs/etc
            # consistent for now
            start = self._last_export
            self._last_export = compat.time_ns()
            return

        if self.before_flush is not None:
            try:
                self.before_flush()
            except Exception:
                LOG.error("Scheduler before_flush hook failed", exc_info=True)
        events = self.recorder.reset()
        start = self._last_export
        self._last_export = compat.time_ns()
        for exp in self.exporters:
            try:
                exp.export(events, start, self._last_export)
            except exporter.ExportError as e:
                LOG.warning("Unable to export profile: %s. Ignoring.", _traceback.format_exception(e))
            except Exception:
                LOG.exception(
                    "Unexpected error while exporting events. "
                    "Please report this bug to https://github.com/DataDog/dd-trace-py/issues"
                )

    def periodic(self):
        start_time = compat.monotonic()
        try:
            self.flush()
        finally:
            self.interval = max(0, self._configured_interval - (compat.monotonic() - start_time))


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
        super(ServerlessScheduler, self).__init__(interval=self.FORCED_INTERVAL, *args, **kwargs)
        self._profiled_intervals: int = 0

    def periodic(self):
        # Check both the number of intervals and time frame to be sure we don't flush, e.g., empty profiles
        if self._profiled_intervals >= self.FLUSH_AFTER_INTERVALS and (compat.time_ns() - self._last_export) >= (
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
