# Exception profiling collector using sys.monitoring (Python 3.12+)
from __future__ import annotations

import logging
import random
import sys
import threading
from types import TracebackType
from typing import Any
from typing import Optional
from typing import Type

from typing_extensions import Self

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector


LOG = logging.getLogger(__name__)
HAS_MONITORING = hasattr(sys, "monitoring")
_current_thread = threading.current_thread
# Exponential distribution gives Poisson process inter-arrival times
_expovariate = random.expovariate


class ExceptionCollector(collector.Collector):
    __slots__ = (
        "max_nframe",
        "sampling_interval",
        "collect_message",
        "_original_excepthook",
        "_total_exceptions",
        "_sampled_exceptions",
        "_sample_counter",
        "_next_sample",
    )

    def __init__(
        self,
        max_nframe: Optional[int] = None,
        sampling_interval: Optional[int] = None,
        collect_message: Optional[bool] = None,
    ) -> None:
        super().__init__()
        self.max_nframe = max_nframe if max_nframe is not None else config.max_frames
        self.sampling_interval = (
            sampling_interval if sampling_interval is not None else getattr(config.exception, "sampling_interval", 100)
        )
        self.collect_message = (
            collect_message if collect_message is not None else getattr(config.exception, "collect_message", True)
        )
        self._original_excepthook = None
        self._total_exceptions = 0
        self._sampled_exceptions = 0
        self._sample_counter = 0
        self._next_sample = self.sampling_interval

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
        LOG.debug("Starting ExceptionCollector")

        if HAS_MONITORING and sys.version_info >= (3, 12):
            try:
                # EXCEPTION_HANDLED fires once per caught exception
                sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "dd-trace-exception-profiler")
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, sys.monitoring.events.EXCEPTION_HANDLED)
                sys.monitoring.register_callback(
                    sys.monitoring.PROFILER_ID,
                    sys.monitoring.events.EXCEPTION_HANDLED,
                    self._on_exception_handled,
                )
                self._original_excepthook = sys.excepthook
                sys.excepthook = self._excepthook
                LOG.debug("Using sys.monitoring.EXCEPTION_HANDLED + excepthook")
            except Exception as e:
                LOG.debug("Failed to set up monitoring: %s", e)
                self._original_excepthook = sys.excepthook
                sys.excepthook = self._excepthook
        else:
            # Fallback for older Python (only catches uncaught exceptions)
            self._original_excepthook = sys.excepthook
            sys.excepthook = self._excepthook
            LOG.debug("Using sys.excepthook only")

        LOG.info("ExceptionCollector started: interval=%d", self.sampling_interval)

    def _stop_service(self) -> None:
        LOG.debug("Stopping ExceptionCollector")

        if HAS_MONITORING and sys.version_info >= (3, 12):
            try:
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, 0)
                sys.monitoring.free_tool_id(sys.monitoring.PROFILER_ID)
            except Exception:
                pass

        if self._original_excepthook:
            sys.excepthook = self._original_excepthook
            self._original_excepthook = None

        LOG.info("ExceptionCollector stopped: total=%d, sampled=%d", self._total_exceptions, self._sampled_exceptions)

    def _on_exception_handled(self, code: Any, instruction_offset: int, exception: BaseException) -> None:
        self._total_exceptions += 1
        self._sample_counter += 1
        if self._sample_counter >= self._next_sample:
            # Exponential inter-arrival time gives Poisson process (single fast call)
            self._next_sample = max(1, int(_expovariate(1.0 / self.sampling_interval)))
            self._sample_counter = 0
            try:
                self._collect_exception(type(exception), exception, exception.__traceback__)
                self._sampled_exceptions += 1
            except Exception:
                pass

    def _excepthook(self, exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: TracebackType) -> None:
        self._total_exceptions += 1
        self._sample_counter += 1
        if self._sample_counter >= self._next_sample:
            self._next_sample = max(1, int(_expovariate(1.0 / self.sampling_interval)))
            self._sample_counter = 0
            try:
                self._collect_exception(exc_type, exc_value, exc_traceback)
                self._sampled_exceptions += 1
            except Exception:
                pass
        if self._original_excepthook:
            self._original_excepthook(exc_type, exc_value, exc_traceback)

    def _collect_exception(self, exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: TracebackType) -> None:
        if not ddup.is_available:
            return

        module = exc_type.__module__
        exception_type = f"{module}.{exc_type.__name__}" if module else exc_type.__name__
        handle = ddup.SampleHandle()

        try:
            handle.push_exceptioninfo(exception_type, 1)

            thread = _current_thread()
            handle.push_threadinfo(thread.ident or 0, getattr(thread, "native_id", 0) or 0, thread.name)

            # Collect frames as tuples (func, filename, lineno)
            tb = exc_traceback
            frames = []
            max_frames = self.max_nframe
            while tb is not None and len(frames) < max_frames:
                code = tb.tb_frame.f_code
                frames.append((code.co_name, code.co_filename, tb.tb_lineno))
                tb = tb.tb_next

            # Push in reverse order (innermost last)
            for i in range(len(frames) - 1, -1, -1):
                func, filename, lineno = frames[i]
                handle.push_frame(func, filename, 0, lineno)

            handle.flush_sample()
        except Exception:
            handle.drop_sample()
            raise

    def snapshot(self) -> None:
        pass

    def get_stats(self) -> dict:
        return {"total_exceptions": self._total_exceptions, "sampled_exceptions": self._sampled_exceptions}
