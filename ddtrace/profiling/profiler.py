# -*- encoding: utf-8 -*-
import logging
import os

import ddtrace
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


def _build_default_exporters(service):
    exporters = []
    if "DD_PROFILING_API_KEY" in os.environ or "DD_API_KEY" in os.environ:
        exporters.append(http.PprofHTTPExporter(service=service))

    _OUTPUT_PPROF = os.environ.get("DD_PROFILING_OUTPUT_PPROF")
    if _OUTPUT_PPROF:
        exporters.append(file.PprofFileExporter(_OUTPUT_PPROF))

    if not exporters:
        LOG.warning("No exporters are configured, no profile will be output")

    return exporters


def _get_service_name():
    for service_name_var in ("DD_SERVICE", "DD_SERVICE_NAME", "DATADOG_SERVICE_NAME"):
        service_name = os.environ.get(service_name_var)
        if service_name is not None:
            return service_name


def _build_default_collectors():
    r = recorder.Recorder()
    return [
        stack.StackCollector(r, tracer=ddtrace.tracer),
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

    service = attr.ib(factory=_get_service_name)
    collectors = attr.ib(factory=_build_default_collectors)
    exporters = attr.ib(default=None)
    schedulers = attr.ib(init=False, factory=list)
    status = attr.ib(init=False, type=ProfilerStatus, default=ProfilerStatus.STOPPED)

    def __attrs_post_init__(self):
        if self.exporters is None:
            self.exporters = _build_default_exporters(self.service)

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

        :param flush: Wait for the flush of the remaining events before stopping.
        """
        for col in reversed(self.collectors):
            col.stop()

        for col in reversed(self.collectors):
            col.join()

        for s in reversed(self.schedulers):
            s.stop()

        if flush:
            for s in reversed(self.schedulers):
                s.join()

        self.status = ProfilerStatus.STOPPED
