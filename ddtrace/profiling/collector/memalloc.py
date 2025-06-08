# -*- encoding: utf-8 -*-
import logging
from math import ceil
import os
import threading
import time
import typing  # noqa:F401
from typing import Optional


try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    logging.getLogger(__name__).debug("failed to import memalloc", exc_info=True)
    _memalloc = None  # type: ignore[assignment]

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.settings.profiling import config


LOG = logging.getLogger(__name__)


class MemoryCollector(collector.PeriodicCollector):
    """Memory allocation collector."""

    _DEFAULT_MAX_EVENTS = 16
    _DEFAULT_INTERVAL = 0.5

    def __init__(
        self,
        _interval: float = _DEFAULT_INTERVAL,
        _max_events: Optional[int] = None,
        max_nframe: Optional[int] = None,
        heap_sample_size: Optional[int] = None,
        ignore_profiler: Optional[bool] = None,
    ):
        super().__init__()
        self._interval: float = _interval
        # TODO make this dynamic based on the 1. interval and 2. the max number of events allowed in the Recorder
        self._max_events: int = _max_events if _max_events is not None else config.memory.events_buffer
        self.max_nframe: int = max_nframe if max_nframe is not None else config.max_frames
        self.heap_sample_size: int = heap_sample_size if heap_sample_size is not None else config.heap.sample_size
        self.ignore_profiler: bool = ignore_profiler if ignore_profiler is not None else config.ignore_profiler

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

        try:
            events = _memalloc.heap()
        except RuntimeError:
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect heap events from process %d", os.getpid(), exc_info=True)
            return tuple()

        for (frames, _, thread_id), size in events:
            if not self.ignore_profiler or thread_id not in thread_id_ignore_set:
                handle = ddup.SampleHandle()
                handle.push_heap(size)
                handle.push_threadinfo(
                    thread_id, _threading.get_thread_native_id(thread_id), _threading.get_thread_name(thread_id)
                )
                try:
                    for frame in frames:
                        handle.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                    handle.flush_sample()
                except AttributeError:
                    # DEV: This might happen if the memalloc sofile is unlinked and relinked without module
                    #      re-initialization.
                    LOG.debug("Invalid state detected in memalloc module, suppressing profile")
        return tuple()

    def collect(self):
        # TODO: The event timestamp is slightly off since it's going to be the time we copy the data from the
        # _memalloc buffer to our Recorder. This is fine for now, but we might want to store the nanoseconds
        # timestamp in C and then return it via iter_events.
        try:
            events_iter, count, alloc_count = _memalloc.iter_events()
        except RuntimeError:
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect memory events from process %d", os.getpid(), exc_info=True)
            return tuple()

        # `events_iter` is a consumable view into `iter_events()`; copy it so we can send it to both pyprof
        # and libdatadog. This will be changed if/when we ever return to only a single possible exporter
        events = list(events_iter)
        thread_id_ignore_set = self._get_thread_id_ignore_set()

        for (frames, _, thread_id), size, _ in events:
            if thread_id in thread_id_ignore_set:
                continue
            handle = ddup.SampleHandle()
            handle.push_monotonic_ns(time.monotonic_ns())
            handle.push_alloc(int((ceil(size) * alloc_count) / count), count)  # Roundup to help float precision
            handle.push_threadinfo(
                thread_id, _threading.get_thread_native_id(thread_id), _threading.get_thread_name(thread_id)
            )
            try:
                for frame in frames:
                    handle.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                handle.flush_sample()
            except AttributeError:
                # DEV: This might happen if the memalloc sofile is unlinked and relinked without module
                #      re-initialization.
                LOG.debug("Invalid state detected in memalloc module, suppressing profile")
        return tuple()
