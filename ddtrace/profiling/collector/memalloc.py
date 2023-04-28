# -*- encoding: utf-8 -*-
import logging
import os
import threading
import typing

import attr


try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    _memalloc = None  # type: ignore[assignment]

from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.settings.profiling import config


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


@attr.s
class MemoryCollector(collector.PeriodicCollector):
    """Memory allocation collector."""

    _DEFAULT_MAX_EVENTS = 16
    _DEFAULT_INTERVAL = 0.5

    # Arbitrary interval to empty the _memalloc event buffer
    _interval = attr.ib(default=_DEFAULT_INTERVAL, repr=False)

    # TODO make this dynamic based on the 1. interval and 2. the max number of events allowed in the Recorder
    _max_events = attr.ib(type=int, default=config.memory.events_buffer)
    max_nframe = attr.ib(default=config.max_frames, type=int)
    heap_sample_size = attr.ib(type=int, default=config.heap.sample_size)
    ignore_profiler = attr.ib(default=config.ignore_profiler, type=bool)

    def _start_service(self):
        # type: (...) -> None
        """Start collecting memory profiles."""
        if _memalloc is None:
            raise collector.CollectorUnavailable

        try:
            _memalloc.start(self.max_nframe, self._max_events, self.heap_sample_size)
        except RuntimeError:
            # This happens on fork because we don't call the shutdown hook since
            # the thread responsible for doing so is not running in the child
            # process. Therefore we stop and restart the collector instead.
            _memalloc.stop()
            _memalloc.start(self.max_nframe, self._max_events, self.heap_sample_size)

        super(MemoryCollector, self)._start_service()

    @staticmethod
    def on_shutdown():
        # type: () -> None
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
        try:
            events, count, alloc_count = _memalloc.iter_events()
        except RuntimeError:
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect memory events from process %d", os.getpid(), exc_info=True)
            return tuple()

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
                for (stack, nframes, thread_id), size, domain in events
                if not self.ignore_profiler or thread_id not in thread_id_ignore_set
            ),
        )
