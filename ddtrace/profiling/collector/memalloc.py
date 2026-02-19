# -*- encoding: utf-8 -*-

from __future__ import annotations

import logging
import os
import time
from types import TracebackType
from typing import TYPE_CHECKING
from typing import Optional
from typing import cast

from typing_extensions import Self


if TYPE_CHECKING:
    # We need the pyright: ignore because pprof_pb2 does not exist as a real module, only as a pyi.
    from tests.profiling.collector import pprof_pb2  # pyright: ignore[reportMissingModuleSource]

try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    logging.getLogger(__name__).debug("failed to import memalloc", exc_info=True)
    _memalloc = None  # type: ignore[assignment]

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector


LOG = logging.getLogger(__name__)


def _get_rss_bytes() -> Optional[int]:
    """Get the current process RSS (Resident Set Size) in bytes.
    Returns None if psutil is unavailable."""
    try:
        from ddtrace.vendor import psutil

        return psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        return None


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
        # AIDEV-NOTE: RSS captured at each snapshot for memory leak detection workflow.
        # Used for RSS decomposition: RSS - managed_heap = native/runtime overhead.
        self.last_rss_bytes: Optional[int] = None

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

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.stop()

    def join(self, timeout: Optional[float] = None) -> None:
        pass

    def stop(self) -> None:
        if _memalloc is not None:
            try:
                _memalloc.stop()
            except RuntimeError:
                LOG.debug("Failed to stop memalloc profiling on shutdown", exc_info=True)

    def snapshot(self) -> None:
        """Take a snapshot of collected data, to be exported."""

        try:
            if _memalloc is None:
                raise ValueError("Memalloc is not initialized")
            _memalloc.heap()  # Samples are exported directly to pprof, no return value needed
        except (RuntimeError, ValueError):
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("Unable to collect heap events from process %d", os.getpid(), exc_info=True)

        if config.memory.leak_detection_enabled:  # pyright: ignore[reportAttributeAccessIssue]
            self.last_rss_bytes = _get_rss_bytes()
            if self.last_rss_bytes is not None:
                self._emit_rss_sample(self.last_rss_bytes)

    def _emit_rss_sample(self, rss_bytes: int) -> None:
        """Emit a synthetic heap sample representing the process RSS.

        This creates a distinguishable sample in the pprof profile with
        a synthetic frame, allowing the backend to correlate RSS with
        heap profiling data at the same point in time.
        """
        try:
            handle = ddup.SampleHandle()
            handle.push_heap(rss_bytes)
            handle.push_monotonic_ns(time.monotonic_ns())
            handle.push_frame("[process RSS]", "[memalloc]", 0, 0)
            handle.flush_sample()
        except Exception:
            LOG.debug("Failed to emit RSS sample", exc_info=True)

    def snapshot_and_parse_pprof(self, output_filename: str, assert_samples: bool = True) -> pprof_pb2.Profile:
        """Export samples to profile, upload, and parse the pprof profile.

        This is similar to test_snapshot() but exports to the profile and returns
        the parsed pprof profile instead of Python objects.

        Args:
            output_filename: The pprof output filename prefix (without .pid.counter suffix)
            assert_samples: Whether to assert that the profile contains samples

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

        return pprof_utils.parse_newest_profile(output_filename, assert_samples=assert_samples)
