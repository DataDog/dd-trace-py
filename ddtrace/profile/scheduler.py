# -*- encoding: utf-8 -*-
import logging

from ddtrace.profile import _attr
from ddtrace.profile import _periodic
from ddtrace.profile import _traceback
from ddtrace.profile import exporter
from ddtrace.vendor import attr

LOG = logging.getLogger(__name__)


@attr.s
class Scheduler(object):
    """Schedule export of recorded data."""

    recorder = attr.ib()
    exporters = attr.ib()
    interval = attr.ib(factory=_attr.from_env("DD_PROFILING_UPLOAD_INTERVAL", 60, float))
    _periodic = attr.ib(init=False, default=None)

    def __enter__(self):
        self.start()
        return self

    def start(self):
        """Start the scheduler."""
        self._periodic = _periodic.PeriodicThread(
            self.interval, self.flush, name="%s:%s" % (__name__, self.__class__.__name__)
        )
        LOG.debug("Starting scheduler")
        self._periodic.start()
        LOG.debug("Scheduler started")

    def __exit__(self, exc_type, exc_value, traceback):
        return self.stop()

    def stop(self, flush=True):
        """Stop the scheduler.

        :param flush: Whetever to do a final flush.
        """
        LOG.debug("Stopping scheduler")
        if self._periodic:
            self._periodic.stop()
            self._periodic.join()
            self._periodic = None
        if flush:
            self.flush()
        LOG.debug("Scheduler stopped")

    def flush(self):
        """Flush events from recorder to exporters."""
        LOG.debug("Flushing events")
        events = self.recorder.reset()
        total_events = sum(len(v) for v in events.values())
        for exp in self.exporters:
            try:
                exp.export(events)
            except exporter.ExportError as e:
                LOG.error("Unable to export %d events: %s", total_events, _traceback.format_exception(e))
            except Exception:
                LOG.exception("Error while exporting %d events", total_events)
