# -*- encoding: utf-8 -*-
"""Exception profiling collector.

This collector monitors Python exceptions and samples them statistically
using Poisson distribution to control overhead.

For Python 3.14+, this uses the sys.monitoring API for efficient exception tracking.
"""

from __future__ import annotations

import logging
import sys
import threading
from types import TracebackType
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Type

import numpy as np
from typing_extensions import Self

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector


if TYPE_CHECKING:
    from ddtrace.internal.datadog.profiling.ddup import SampleHandle


LOG = logging.getLogger(__name__)

# Check if sys.monitoring is available (Python 3.12+)
HAS_MONITORING = hasattr(sys, "monitoring")


class ExceptionSampler:
    """Statistical sampler for exceptions using Poisson distribution."""

    def __init__(self, sampling_interval: int = 100):
        """Initialize the sampler.

        Args:
            sampling_interval: Average number of exceptions between samples
        """
        self.sampling_interval = sampling_interval
        self.rng = np.random.default_rng()
        self._lock = threading.Lock()
        self._reset_interval()

    def _reset_interval(self) -> None:
        """Reset the sampling interval using Poisson distribution."""
        # Poisson distribution gives us the time until next event
        self.next_sample = int(self.rng.poisson(self.sampling_interval))
        if self.next_sample == 0:
            self.next_sample = 1  # Always wait at least 1 exception

    def should_sample(self) -> bool:
        """Check if we should sample the current exception.

        Returns:
            True if this exception should be sampled, False otherwise
        """
        with self._lock:
            self.next_sample -= 1
            if self.next_sample <= 0:
                self._reset_interval()
                return True
            return False


class ExceptionCollector(collector.Collector):
    """Collects exception data for profiling."""

    def __init__(
        self,
        max_nframe: Optional[int] = None,
        sampling_interval: Optional[int] = None,
        collect_message: Optional[bool] = None,
    ) -> None:
        """Initialize the exception collector.

        Args:
            max_nframe: Maximum number of stack frames to collect
            sampling_interval: Average number of exceptions between samples
            collect_message: Whether to collect exception messages
        """
        super().__init__()
        self.max_nframe = max_nframe if max_nframe is not None else config.max_frames
        self.sampling_interval = (
            sampling_interval if sampling_interval is not None
            else getattr(config.exception, "sampling_interval", 100)
        )
        self.collect_message = (
            collect_message if collect_message is not None
            else getattr(config.exception, "collect_message", True)
        )

        self.sampler = ExceptionSampler(self.sampling_interval)
        self._original_excepthook = None
        self._stats = {
            "total_exceptions": 0,
            "sampled_exceptions": 0,
        }
        self._lock = threading.Lock()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.stop()

    def _start_service(self) -> None:
        """Start the exception collector."""
        LOG.debug("Starting ExceptionCollector")

        if HAS_MONITORING and sys.version_info >= (3, 12):
            # Use sys.monitoring for Python 3.12+
            # RAISE event is triggered when an exception is raised (both caught and uncaught)
            try:
                sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "dd-trace-exception-profiler")
                # Use RAISE to catch ALL exceptions (both caught and uncaught)
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, sys.monitoring.events.RAISE)
                sys.monitoring.register_callback(
                    sys.monitoring.PROFILER_ID,
                    sys.monitoring.events.RAISE,
                    self._monitoring_exception_callback
                )
                LOG.debug("Using sys.monitoring.RAISE for exception profiling")
            except Exception as e:
                LOG.debug("Failed to set up monitoring, falling back to excepthook: %s", e)
                # Fallback to excepthook if monitoring fails
                self._original_excepthook = sys.excepthook
                sys.excepthook = self._excepthook
        else:
            # Fallback to sys.excepthook for older Python versions
            # Note: This only catches uncaught exceptions
            self._original_excepthook = sys.excepthook
            sys.excepthook = self._excepthook
            LOG.debug("Using sys.excepthook for exception profiling (only uncaught exceptions)")

        LOG.info(
            "ExceptionCollector started with sampling_interval=%d, collect_message=%s",
            self.sampling_interval,
            self.collect_message
        )

    def _stop_service(self) -> None:
        """Stop the exception collector."""
        LOG.debug("Stopping ExceptionCollector")

        if HAS_MONITORING and sys.version_info >= (3, 12):
            try:
                # Disable monitoring
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, 0)
                sys.monitoring.free_tool_id(sys.monitoring.PROFILER_ID)
            except Exception:
                pass  # Monitoring might not have been set up

        # Restore the original excepthook if it was replaced
        if self._original_excepthook:
            sys.excepthook = self._original_excepthook
            self._original_excepthook = None

        LOG.info(
            "ExceptionCollector stopped. Stats: total=%d, sampled=%d",
            self._stats["total_exceptions"],
            self._stats["sampled_exceptions"]
        )

    def _monitoring_exception_callback(
        self,
        code: Any,
        instruction_offset: int,
        exception: BaseException,
    ) -> None:
        """Callback for sys.monitoring exception events (Python 3.12+).

        Args:
            code: Code object where exception occurred
            instruction_offset: Bytecode offset of the exception
            exception: The exception instance
        """
        # Only count exceptions at their origin (tb_depth=1)
        # The RAISE event fires for every frame the exception passes through,
        # but we only want to count it once at its origin
        tb = exception.__traceback__
        if tb is None or tb.tb_next is not None:
            # Either no traceback yet, or depth > 1 (propagating)
            return

        # Update stats
        with self._lock:
            self._stats["total_exceptions"] += 1

        # Check if we should sample this exception
        if self.sampler.should_sample():
            try:
                exc_type = type(exception)
                exc_traceback = exception.__traceback__
                self._collect_exception(exc_type, exception, exc_traceback)
                with self._lock:
                    self._stats["sampled_exceptions"] += 1
            except Exception:
                LOG.debug("Failed to collect exception sample", exc_info=True)

    def _excepthook(
        self,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType,
    ) -> None:
        """Hook called when an uncaught exception occurs (fallback for older Python).

        Args:
            exc_type: Exception type
            exc_value: Exception instance
            exc_traceback: Traceback object
        """
        # Update stats
        with self._lock:
            self._stats["total_exceptions"] += 1

        # Check if we should sample this exception
        if self.sampler.should_sample():
            try:
                self._collect_exception(exc_type, exc_value, exc_traceback)
                with self._lock:
                    self._stats["sampled_exceptions"] += 1
            except Exception:
                LOG.debug("Failed to collect exception sample", exc_info=True)

        # Call the original excepthook
        if self._original_excepthook:
            self._original_excepthook(exc_type, exc_value, exc_traceback)

    def _collect_exception(
        self,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType,
    ) -> None:
        """Collect exception data and send it to the profiler.

        Args:
            exc_type: Exception type
            exc_value: Exception instance
            exc_traceback: Traceback object
        """
        # Get exception type name
        exception_type = f"{exc_type.__module__}.{exc_type.__name__}" if exc_type.__module__ else exc_type.__name__

        # Get exception message if enabled
        exception_message = None
        if self.collect_message:
            try:
                exception_message = str(exc_value)
            except Exception:
                exception_message = "<error getting message>"

        frames = []
        tb = exc_traceback
        frame_count = 0

        while tb is not None and frame_count < self.max_nframe:
            frame = tb.tb_frame
            code = frame.f_code

            frames.append({
                "filename": code.co_filename,
                "function": code.co_name,
                "lineno": tb.tb_lineno,
            })

            tb = tb.tb_next
            frame_count += 1

        try:
            self._push_sample(exception_type, exception_message, frames)
        except Exception:
            LOG.debug("Failed to push exception sample", exc_info=True)

    def _push_sample(
        self,
        exception_type: str,
        exception_message: Optional[str],
        frames: list,
    ) -> None:
        """Push exception sample to the profiler using ddup.

        Args:
            exception_type: Exception type name
            exception_message: Exception message (optional)
            frames: List of stack frame information
        """
        if not ddup.is_available:
            LOG.debug("ddup not available, skipping exception sample")
            return

        # Create sample handle
        handle = ddup.SampleHandle()

        try:
            # Add exception type and count
            handle.push_exceptioninfo(exception_type, 1)

            import threading
            current_thread = threading.current_thread()
            handle.push_threadinfo(
                current_thread.ident or 0,
                current_thread.native_id if hasattr(current_thread, "native_id") else 0,
                current_thread.name
            )

            # Add stack frames (reversed so innermost frame is last)
            for frame in reversed(frames):
                handle.push_frame(
                    frame["function"],
                    frame["filename"],
                    0,  # address
                    frame["lineno"]
                )

            # Flush the sample to send it
            handle.flush_sample()

        except Exception:
            # If we fail, drop the sample to avoid memory leak
            handle.drop_sample()
            raise

    def snapshot(self) -> None:
        """Take a snapshot of collected data (no-op for exceptions)."""
        pass

    def get_stats(self) -> dict:
        """Get collector statistics.

        Returns:
            Dictionary with exception collection stats
        """
        with self._lock:
            return self._stats.copy()
