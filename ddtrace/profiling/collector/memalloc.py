# -*- encoding: utf-8 -*-
import logging
import math
import os
import threading
import typing

import attr


try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    _memalloc = None  # type: ignore[assignment]

from ddtrace.internal.utils import attr as attr_utils
from ddtrace.internal.utils import formats
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling import event


LOG = logging.getLogger(__name__)


@event.event_class
class MemoryAllocSampleEvent(event.StackBasedEvent):
    """A sample storing memory allocation tracked."""

    size = attr.ib(default=0, type=int)
    """Allocation size in bytes."""

    capture_pct = attr.ib(default=None, type=float)
    """The capture percentage."""

    nevents = attr.ib(default=0, type=int)
    """The total number of allocation events sampled."""


@event.event_class
class MemoryHeapSampleEvent(event.StackBasedEvent):
    """A sample storing memory allocation tracked."""

    size = attr.ib(default=0, type=int)
    """Allocation size in bytes."""

    sample_size = attr.ib(default=0, type=int)
    """The sampling size."""


def _get_default_heap_sample_size(
    default_heap_sample_size=1024 * 1024,  # type: int
):
    # type: (...) -> int
    heap_sample_size = os.environ.get("DD_PROFILING_HEAP_SAMPLE_SIZE")
    if heap_sample_size is not None:
        return int(heap_sample_size)

    if not formats.asbool(os.environ.get("DD_PROFILING_HEAP_ENABLED", "1")):
        return 0

    try:
        from ddtrace.vendor import psutil

        total_mem = psutil.swap_memory().total + psutil.virtual_memory().total
    except Exception:
        LOG.warning(
            "Unable to get total memory available, using default value of %d KB",
            default_heap_sample_size / 1024,
            exc_info=True,
        )
        return default_heap_sample_size

    # This is TRACEBACK_ARRAY_MAX_COUNT
    max_samples = 2 ** 16

    return max(math.ceil(total_mem / max_samples), default_heap_sample_size)


@attr.s
class MemoryCollector(collector.PeriodicCollector):
    """Memory allocation collector."""

    _DEFAULT_MAX_EVENTS = 16
    _DEFAULT_INTERVAL = 0.5

    # Arbitrary interval to empty the _memalloc event buffer
    _interval = attr.ib(default=_DEFAULT_INTERVAL, repr=False)

    # TODO make this dynamic based on the 1. interval and 2. the max number of events allowed in the Recorder
    _max_events = attr.ib(factory=attr_utils.from_env("_DD_PROFILING_MEMORY_EVENTS_BUFFER", _DEFAULT_MAX_EVENTS, int))
    max_nframe = attr.ib(factory=attr_utils.from_env("DD_PROFILING_MAX_FRAMES", 64, int))
    heap_sample_size = attr.ib(type=int, factory=_get_default_heap_sample_size)
    ignore_profiler = attr.ib(factory=attr_utils.from_env("DD_PROFILING_IGNORE_PROFILER", False, formats.asbool))

    def _start_service(self):
        # type: (...) -> None
        """Start collecting memory profiles."""
        if _memalloc is None:
            raise collector.CollectorUnavailable

        _memalloc.start(self.max_nframe, self._max_events, self.heap_sample_size)

        super(MemoryCollector, self)._start_service()

    def _stop_service(self):
        # type: (...) -> None
        super(MemoryCollector, self)._stop_service()

        if _memalloc is not None:
            try:
                _memalloc.stop()
            except RuntimeError:
                pass

    def _get_thread_id_ignore_set(self):
        # type: () -> typing.Set[int]
        # This method is not perfect and prone to race condition in theory, but very little in practice.
        # Anyhow it's not a big deal — it's a best effort feature.
        return {
            thread.ident
            for thread in threading.enumerate()
            if getattr(thread, "_ddtrace_profiling_ignore", False) and thread.ident is not None
        }

    def snapshot(self):
        thread_id_ignore_set = self._get_thread_id_ignore_set()
        return (
            tuple(
                MemoryHeapSampleEvent(
                    thread_id=thread_id,
                    thread_name=_threading.get_thread_name(thread_id),
                    thread_native_id=_threading.get_thread_native_id(thread_id),
                    frames=stack,
                    nframes=nframes,
                    size=size,
                    sample_size=self.heap_sample_size,
                )
                for (stack, nframes, thread_id), size in _memalloc.heap()
                if not self.ignore_profiler or thread_id not in thread_id_ignore_set
            ),
        )

    def collect(self):
        events, count, alloc_count = _memalloc.iter_events()
        capture_pct = 100 * count / alloc_count
        thread_id_ignore_set = self._get_thread_id_ignore_set()
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
                if not self.ignore_profiler or thread_id not in thread_id_ignore_set
            ),
        )
