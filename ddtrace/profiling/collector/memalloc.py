from __future__ import annotations

import logging
import os
from types import TracebackType
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional
from typing import cast

from typing_extensions import Self


if TYPE_CHECKING:
    # We need the pyright: ignore because pprof_pb2 does not exist as a real module, only as a pyi.
    from tests.profiling.collector import pprof_pb2  # pyright: ignore[reportMissingModuleSource]

try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    logging.getLogger(__name__).debug("[profiling] failed to import memalloc", exc_info=True)
    _memalloc = None  # type: ignore[assignment]

from ddtrace.internal import service
from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector import threading as collector_threading


LOG = logging.getLogger(__name__)

_thread_name_hooks_installed: bool = False


def _install_thread_name_hooks() -> None:
    """Record thread names when the stack profiler hooks are not installed.

    The stack hooks also record names. A second wrapper around the same Thread
    methods would hide those hooks.
    """
    global _thread_name_hooks_installed

    if _thread_name_hooks_installed or not ddup.is_available:
        return
    if collector_threading._installed_thread_hooks:
        return
    _thread_name_hooks_installed = True

    set_native_id = cast(
        Callable[..., None],
        ddtrace_threading.Thread._set_native_id,  # type: ignore[attr-defined]
    )
    bootstrap_inner = cast(
        Callable[..., None],
        ddtrace_threading.Thread._bootstrap_inner,  # type: ignore[attr-defined]
    )

    def thread_set_native_id(self: ddtrace_threading.Thread, *args: Any, **kwargs: Any) -> None:
        set_native_id(self, *args, **kwargs)
        # CPython calls _set_native_id before setting the started event, so an exception here
        # would make Thread.start block forever.
        if self.ident is None:
            return
        try:
            ddup.register_thread_name(self.ident, self.name)
        except Exception:
            LOG.debug("[profiling] Failed to register thread name for %r", self.ident, exc_info=True)

    def thread_bootstrap_inner(self: ddtrace_threading.Thread, *args: Any, **kwargs: Any) -> None:
        bootstrap_inner(self, *args, **kwargs)
        if self.ident is None:
            return
        try:
            ddup.unregister_thread_name(self.ident)
        except Exception:
            LOG.debug("[profiling] Failed to unregister thread name for %r", self.ident, exc_info=True)

    ddtrace_threading.Thread._set_native_id = thread_set_native_id  # type: ignore[attr-defined]
    ddtrace_threading.Thread._bootstrap_inner = thread_bootstrap_inner  # type: ignore[attr-defined]


class MemoryCollector:
    """Memory allocation collector."""

    def __init__(
        self,
        max_nframe: Optional[int] = None,
        heap_sample_size: Optional[int] = None,
        ignore_profiler: Optional[bool] = None,
        mem_domain_enabled: Optional[bool] = None,
    ) -> None:
        self.max_nframe = cast(int, max_nframe if max_nframe is not None else config.max_frames)
        self.heap_sample_size = cast(
            int,
            heap_sample_size if heap_sample_size is not None else config.heap.sample_size,  # pyright: ignore
        )
        self.ignore_profiler = cast(bool, ignore_profiler if ignore_profiler is not None else config.ignore_profiler)
        mem_default: bool = config.memory.mem_domain_enabled
        self.mem_domain_enabled = mem_domain_enabled if mem_domain_enabled is not None else mem_default

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        attrs = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        attrs_str = ", ".join(f"{k}={v!r}" for k, v in attrs.items())
        return f"{class_name}({attrs_str})"

    @property
    def status(self) -> service.ServiceStatus:
        if _memalloc is not None and _memalloc.is_started():
            return service.ServiceStatus.RUNNING

        return service.ServiceStatus.STOPPED

    def start(self) -> None:
        """Start collecting memory profiles."""
        if _memalloc is None:
            raise collector.CollectorUnavailable

        _install_thread_name_hooks()
        collector_threading.init_thread_names()

        try:
            _memalloc.start(self.max_nframe, self.heap_sample_size, self.mem_domain_enabled)
        except RuntimeError:
            # This happens on fork because we don't call the shutdown hook since
            # the thread responsible for doing so is not running in the child
            # process. Therefore we stop and restart the collector instead.
            _memalloc.stop()
            _memalloc.start(self.max_nframe, self.heap_sample_size, self.mem_domain_enabled)

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
                LOG.debug("[profiling] Failed to stop memalloc profiling on shutdown", exc_info=True)

    def _rollback_start(self) -> None:
        if _memalloc is not None and _memalloc.is_started():
            _memalloc.stop()

    def snapshot(self) -> None:
        """Take a snapshot of collected data, to be exported."""

        try:
            if _memalloc is None:
                raise ValueError("Memalloc is not initialized")
            _memalloc.heap()  # Samples are exported directly to pprof, no return value needed
        except (RuntimeError, ValueError):
            # DEV: This can happen if either _memalloc has not been started or has been stopped.
            LOG.debug("[profiling] Unable to collect heap events from process %d", os.getpid(), exc_info=True)

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
