# -*- encoding: utf-8 -*-
import logging

import attr

from ddtrace.internal import compat
from ddtrace.internal import periodic
from ddtrace.internal.utils import attr as attr_utils
from ddtrace.profiling import _traceback
from ddtrace.profiling import exporter


LOG = logging.getLogger(__name__)


@attr.s
class Scheduler(periodic.PeriodicService):
    """Schedule export of recorded data."""

    recorder = attr.ib()
    exporters = attr.ib()
    before_flush = attr.ib(default=None, eq=False)
    _interval = attr.ib(factory=attr_utils.from_env("DD_PROFILING_UPLOAD_INTERVAL", 60.0, float))
    _configured_interval = attr.ib(init=False)
    _last_export = attr.ib(init=False, default=None, eq=False)

    def __attrs_post_init__(self):
        # Copy the value to use it later since we're going to adjust the real interval
        self._configured_interval = self.interval

    def _start_service(self):  # type: ignore[override]
        # type: (...) -> None
        """Start the scheduler."""
        LOG.debug("Starting scheduler")
        super(Scheduler, self)._start_service()
        self._last_export = compat.time_ns()
        LOG.debug("Scheduler started")

    def flush(self):
        """Flush events from recorder to exporters."""
        LOG.debug("Flushing events")
        if self.before_flush is not None:
            try:
                self.before_flush()
            except Exception:
                LOG.error("Scheduler before_flush hook failed", exc_info=True)
        if self.exporters:
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


@attr.s
class ServerlessScheduler(Scheduler):
    """Serverless scheduler that works on, e.g., AWS Lambda.

    The idea with this scheduler is to not sleep 60s, but to sleep 1s and flush out profiles after 60 sleeping period.
    As the service can be frozen a few seconds after flushing out a profile, we want to make sure the next flush is not
    > 60s later, but after at least 60 periods of 1s.

    """
    _interval = attr.ib(default=1.0, type=float)
    _profiled_intervals = attr.ib(init=False, default=0)

    def periodic(self):
        now = compat.time_ns()
        if (now - self._last_export) >= 60 * 1e9 and self._profiled_intervals >= 60:
            self._profiled_intervals = 0
            super(ServerlessScheduler, self).periodic()
            # Override interval so it's always 1
            self.interval = 1.0
        else:
            self._profiled_intervals += 1
