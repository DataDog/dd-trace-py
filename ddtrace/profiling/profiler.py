# -*- encoding: utf-8 -*-
import logging
import os

from ddtrace.profiling import recorder
from ddtrace.profiling import scheduler
from ddtrace.vendor import attr
from ddtrace.profiling.collector import exceptions
from ddtrace.profiling.collector import memory
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading
from ddtrace.profiling.exporter import file
from ddtrace.profiling.exporter import http


LOG = logging.getLogger(__name__)


def _build_default_exporters():
    exporters = []
    if "DD_PROFILING_API_KEY" in os.environ:
        exporters.append(http.PprofHTTPExporter())

    _OUTPUT_PPROF = os.environ.get("DD_PROFILING_OUTPUT_PPROF")
    if _OUTPUT_PPROF:
        exporters.append(file.PprofFileExporter(_OUTPUT_PPROF))

    if not exporters:
        LOG.warning("No exporters are configured, no profile will be output")

    return exporters


def _build_default_collectors():
    r = recorder.Recorder()
    return [
        stack.StackCollector(r),
        memory.MemoryCollector(r),
        exceptions.UncaughtExceptionCollector(r),
        threading.LockCollector(r),
    ]


# This ought to use `enum.Enum`, but since it's not available in Python 2, we just use a dumb class.
@attr.s(repr=False)
class ProfilerStatus(object):
    """A Profiler status."""

    status = attr.ib()

    def __repr__(self):
        return self.status.upper()


ProfilerStatus.STOPPED = ProfilerStatus("stopped")
ProfilerStatus.RUNNING = ProfilerStatus("running")


@attr.s
class Profiler(object):
    """Run profiling while code is executed.

    Note that the whole Python process is profiled, not only the code executed. Data from all running threads are
    caught.

    If no collectors are provided, default ones are created.
    If no exporters are provided, default ones are created.

    """

    collectors = attr.ib(factory=_build_default_collectors)
    exporters = attr.ib(factory=_build_default_exporters)
    schedulers = attr.ib(init=False, factory=list)
    status = attr.ib(init=False, type=ProfilerStatus, default=ProfilerStatus.STOPPED)
    statistics = attr.ib(default=False)

    def __attrs_post_init__(self):
        if self.exporters:
            for rec in self.recorders:
                self.schedulers.append(scheduler.Scheduler(recorder=rec, exporters=self.exporters))

    @property
    def recorders(self):
        return set(c.recorder for c in self.collectors)

    def start(self):
        """Start the profiler."""
        for col in self.collectors:
            try:
                col.start()
            except RuntimeError:
                # `tracemalloc` is unavailable?
                pass

        for s in self.schedulers:
            s.start()

        self.status = ProfilerStatus.RUNNING

    def stop(self, flush=True):
        """Stop the profiler.

        This stops all the collectors and schedulers, waiting for them to finish their operations.

        :param flush: Flush the event before stopping.
        """
        for col in reversed(self.collectors):
            col.stop()

        for s in reversed(self.schedulers):
            s.stop(flush=flush)

        self.status = ProfilerStatus.STOPPED
