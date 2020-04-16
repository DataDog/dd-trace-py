import os.path
import sys

try:
    import tracemalloc
except ImportError:
    tracemalloc = None

from ddtrace.profiling import _attr
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.utils import formats
from ddtrace.vendor import attr


_MODULE_TOP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@event.event_class
class MemorySampleEvent(event.SampleEvent):
    """A sample storing memory allocation tracked."""

    snapshot = attr.ib(default=None)
    sampling_pct = attr.ib(default=None)


@attr.s
class MemoryCollector(collector.PeriodicCollector, collector.CaptureSamplerCollector):
    """Memory allocation collector."""

    # Arbitrary interval to use for enabling/disabling tracemalloc
    _interval = attr.ib(default=0.1, repr=False)

    nframes = attr.ib(factory=_attr.from_env("DD_PROFILING_MAX_FRAMES", 64, int))
    ignore_profiler = attr.ib(factory=_attr.from_env("DD_PROFILING_IGNORE_PROFILER", True, formats.asbool))

    def __attrs_post_init__(self):
        if sys.version_info[:2] <= (3, 5):
            self._filter_profiler = self._filter_profiler_35

    @staticmethod
    def _filter_profiler(traces):
        return [trace for trace in traces if all(map(lambda frame: not frame[0].startswith(_MODULE_TOP_DIR), trace[2]))]

    @staticmethod
    def _filter_profiler_35(traces):
        # Python <= 3.5 does not have support for domain
        return [trace for trace in traces if all(map(lambda frame: not frame[0].startswith(_MODULE_TOP_DIR), trace[1]))]

    def start(self):
        """Start collecting memory profiles."""
        if tracemalloc is None:
            raise RuntimeError("tracemalloc is unavailable")
        super(MemoryCollector, self).start()

    def stop(self):
        if tracemalloc is not None:
            tracemalloc.stop()
            super(MemoryCollector, self).stop()

    def collect(self):
        try:
            snapshot = tracemalloc.take_snapshot()
        except RuntimeError:
            events = []
        else:
            tracemalloc.stop()

            if snapshot.traces and self.ignore_profiler:
                snapshot.traces._traces = self._filter_profiler(snapshot.traces._traces)

            if snapshot.traces:
                events = [MemorySampleEvent(snapshot=snapshot, sampling_pct=self.capture_pct)]
            else:
                events = []

        if self._capture_sampler.capture():
            tracemalloc.start(self.nframes)

        return [events]
