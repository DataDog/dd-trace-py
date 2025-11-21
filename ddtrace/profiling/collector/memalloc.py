# -*- encoding: utf-8 -*-
import logging
import os
import threading
from types import TracebackType
from typing import Any
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
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector


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

        # Ensure _threading module is imported before starting memalloc
        # This ensures that the C++ code can access thread name functions
        # when it initializes during memalloc_start()
        import ddtrace.profiling._threading  # noqa: F401

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
        try:
            if _memalloc is None:
                raise ValueError("Memalloc is not initialized")
            _memalloc.heap()  # Samples are exported directly to pprof, no return value needed
        except (RuntimeError, ValueError):
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect heap events from process %d", os.getpid(), exc_info=True)
            return tuple()

        # Note: events are now exported directly to pprof, so we return empty samples
        return tuple()

    def test_snapshot(self) -> Tuple[MemorySample, ...]:
        try:
            if _memalloc is None:
                raise ValueError("Memalloc is not initialized")
            _memalloc.heap()  # Samples are exported directly to pprof, no return value needed
        except (RuntimeError, ValueError):
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect heap events from process %d", os.getpid(), exc_info=True)
            return tuple()

        # Note: events are now exported directly to pprof, so we return empty samples
        return tuple()

    def snapshot_and_parse_pprof(self, output_filename: str) -> Any:
        """Export samples to profile, upload, and parse the pprof profile.

        This is similar to test_snapshot() but exports to the profile and returns
        the parsed pprof profile instead of Python objects.

        Args:
            output_filename: The pprof output filename prefix (without .pid.counter suffix)

        Returns:
            Parsed pprof profile object (pprof_pb2.Profile)

        Raises:
            ImportError: If pprof_utils is not available (only available in test environment)
        """
        # Export samples to profile
        self.snapshot()

        # Upload to write profile to disk
        ddup.upload()

        # Parse the profile (only available in test environment)
        try:
            from tests.profiling.collector import pprof_utils
        except ImportError:
            raise ImportError(
                "pprof_utils is not available. snapshot_and_parse_pprof() is only available in test environment."
            )

        return pprof_utils.parse_newest_profile(output_filename)

    def collect(self) -> Tuple[MemorySample, ...]:
        return tuple()
