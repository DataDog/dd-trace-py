import os.path

try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    _memalloc = None

from ddtrace.profiling import _attr
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.profiling.collector import _threading
from ddtrace.utils import formats
from ddtrace.vendor import attr


_MODULE_TOP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@event.event_class
class MemoryAllocSampleEvent(event.StackBasedEvent):
    """A sample storing memory allocation tracked."""

    size = attr.ib(default=None)
    """Allocation size in bytes."""

    capture_pct = attr.ib(default=None)
    """The capture percentage."""

    nevents = attr.ib(default=None)
    """The total number of allocation events sampled."""


@attr.s
class MemoryCollector(collector.PeriodicCollector):
    """Memory allocation collector."""

    _DEFAULT_MAX_EVENTS = 32
    _DEFAULT_INTERVAL = 0.5

    # Arbitrary interval to empty the _memalloc event buffer
    _interval = attr.ib(default=_DEFAULT_INTERVAL, repr=False)

    # TODO make this dynamic based on the 1. interval and 2. the max number of events allowed in the Recorder
    _max_events = attr.ib(factory=_attr.from_env("_DD_PROFILING_MEMORY_EVENTS_BUFFER", _DEFAULT_MAX_EVENTS, int))
    max_nframe = attr.ib(factory=_attr.from_env("DD_PROFILING_MAX_FRAMES", 64, int))
    ignore_profiler = attr.ib(factory=_attr.from_env("DD_PROFILING_IGNORE_PROFILER", True, formats.asbool))

    def start(self):
        """Start collecting memory profiles."""
        if _memalloc is None:
            raise RuntimeError("memalloc is unavailable")
        _memalloc.start(self.max_nframe, self._max_events)
        super(MemoryCollector, self).start()

    def stop(self):
        if _memalloc is not None:
            try:
                _memalloc.stop()
            except RuntimeError:
                pass
            super(MemoryCollector, self).stop()

    def collect(self):
        events, count, alloc_count = _memalloc.iter_events()
        capture_pct = 100 * count / alloc_count
        # TODO: The event timestamp is slightly off since it's going to be the time we copy the data from the
        # _memalloc buffer to our Recorder. This is fine for now, but we might want to store the nanoseconds
        # timestamp in C and then return it via iter_events.
        return (
            tuple(
                MemoryAllocSampleEvent(
                    thread_id=thread_id,
                    thread_name=_threading.get_thread_name(thread_id),
                    thread_native_id=_threading.get_thread_native_id(thread_id),
                    frames=stack,
                    nframes=nframes,
                    size=size,
                    capture_pct=capture_pct,
                    nevents=alloc_count,
                )
                for (stack, nframes, thread_id), size in events
                # TODO: this should be implemented in _memalloc directly so we have more space for samples
                # not coming from the profiler
                if not self.ignore_profiler or not any(frame[0].startswith(_MODULE_TOP_DIR) for frame in stack)
            ),
        )
