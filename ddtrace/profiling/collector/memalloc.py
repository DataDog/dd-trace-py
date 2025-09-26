# -*- encoding: utf-8 -*-
import logging
import os
import threading
from types import TracebackType
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Type
from typing import cast

from ddtrace.profiling.event import DDFrame


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


class MemorySample(NamedTuple):
    frames: List[DDFrame]
    size: int
    count: (  # pyright: ignore[reportIncompatibleMethodOverride] (count is a method of tuple)
        int  # type: ignore[assignment]
    )
    in_use_size: int
    alloc_size: int
    thread_id: int


class MemoryCollector:
    """Memory allocation collector."""

    def __init__(
        self,
        max_nframe: Optional[int] = None,
        heap_sample_size: Optional[int] = None,
        ignore_profiler: Optional[bool] = None,
    ) -> None:
        self.max_nframe = cast(int, max_nframe if max_nframe is not None else config.max_frames)
        self.heap_sample_size = cast(
            int,
            heap_sample_size if heap_sample_size is not None else config.heap.sample_size,  # pyright: ignore
        )
        self.ignore_profiler = cast(bool, ignore_profiler if ignore_profiler is not None else config.ignore_profiler)

    def start(self) -> None:
        """Start collecting memory profiles."""
        if _memalloc is None:
            raise collector.CollectorUnavailable

        try:
            _memalloc.start(self.max_nframe, self.heap_sample_size)
        except RuntimeError:
            # This happens on fork because we don't call the shutdown hook since
            # the thread responsible for doing so is not running in the child
            # process. Therefore we stop and restart the collector instead.
            _memalloc.stop()
            _memalloc.start(self.max_nframe, self.heap_sample_size)

    def __enter__(self) -> None:
        self.start()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.stop()

    def join(self) -> None:
        pass

    def stop(self) -> None:
        if _memalloc is not None:
            try:
                _memalloc.stop()
            except RuntimeError:
                LOG.debug("Failed to stop memalloc profiling on shutdown", exc_info=True)

    def _get_thread_id_ignore_set(self) -> Set[int]:
        # This method is not perfect and prone to race condition in theory, but very little in practice.
        # Anyhow it's not a big deal — it's a best effort feature.
        return {
            thread.ident
            for thread in threading.enumerate()
            if getattr(thread, "_ddtrace_profiling_ignore", False) and thread.ident is not None
        }

    def snapshot(self) -> Tuple[MemorySample, ...]:
        thread_id_ignore_set = self._get_thread_id_ignore_set()

        try:
            if _memalloc is None:
                raise ValueError("Memalloc is not initialized")
            events = _memalloc.heap()
        except (RuntimeError, ValueError):
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect heap events from process %d", os.getpid(), exc_info=True)
            return tuple()

        for event in events:
            (frames, thread_id), in_use_size, alloc_size, count = event

            if not self.ignore_profiler or thread_id not in thread_id_ignore_set:
                handle = ddup.SampleHandle()

                if in_use_size > 0:
                    handle.push_heap(in_use_size)
                if alloc_size > 0:
                    handle.push_alloc(alloc_size, count)

                handle.push_threadinfo(
                    thread_id,
                    _threading.get_thread_native_id(thread_id),
                    _threading.get_thread_name(thread_id),
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

    def test_snapshot(self) -> Tuple[MemorySample, ...]:
        thread_id_ignore_set = self._get_thread_id_ignore_set()

        try:
            if _memalloc is None:
                raise ValueError("Memalloc is not initialized")
            events = _memalloc.heap()
        except (RuntimeError, ValueError):
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect heap events from process %d", os.getpid(), exc_info=True)
            return tuple()

        samples: List[MemorySample] = []
        for event in events:
            (frames, thread_id), in_use_size, alloc_size, count = event

            if not self.ignore_profiler or thread_id not in thread_id_ignore_set:
                size = in_use_size if in_use_size > 0 else alloc_size

                samples.append(MemorySample(frames, size, count, in_use_size, alloc_size, thread_id))

        return tuple(samples)

    def collect(self) -> Tuple[MemorySample, ...]:
        return tuple()
