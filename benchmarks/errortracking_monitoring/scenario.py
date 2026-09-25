"""Microbenchmark for the shared ``sys.monitoring`` multiplexer.

Measures the overhead of the ``EXCEPTION_HANDLED`` global-handler path used by
error-tracking on Python 3.12+.  The benchmark exercises a tight try/except
loop so the ``on_exception_handled`` callback fires on every iteration.

Configurations (direct ``sys.monitoring`` vs. multiplexer ``register_global``):

- ``direct_passive`` — raw ``sys.monitoring`` callback that does no work
- ``direct_active`` — raw ``sys.monitoring`` callback that counts exceptions
- ``direct_global_passive`` — ``register_global`` handler, no work (falls back to
  direct callback when ``register_global`` is unavailable)
- ``direct_global_active`` — ``register_global`` handler that counts exceptions
  (falls back to a direct callback when ``register_global`` is unavailable)
- ``production_handler`` — the real Error Tracking collector and reporting callback
  with an active span
- ``module_filter_miss`` — module-only filtering with high-cardinality rejected filenames
- ``module_filter_hit`` — module-only filtering with configured filenames

The ``direct_*`` configs reproduce the pre-multiplexer code path (a dedicated
tool slot with a single callback registered directly via
``sys.monitoring.register_callback``).  The ``direct_global_*`` configs use the
shared multiplexer's ``register_global`` / ``unregister_global`` API when
available, opting into direct delivery when supported; otherwise they fall
back to an equivalent direct callback so comparison output remains meaningful.
"""

from collections.abc import Generator
from inspect import signature
import os
from types import CodeType
from typing import Any
from typing import Callable

import bm


class ErrorTrackingMonitoring(bm.Scenario):  # type: ignore[misc]
    handler: str

    def run(self) -> Generator[Callable[[int], None], None]:
        import sys

        sys_monitoring = getattr(sys, "monitoring")
        tool_id = 3
        event = sys_monitoring.events.EXCEPTION_HANDLED
        cleanup: Callable[[], None] | None = None

        # -- module filename filtering -----------------------------------------

        if self.handler in ("module_filter_miss", "module_filter_hit", "module_filter_warm"):
            from ddtrace.errortracking._handled_exceptions import monitoring_reporting as reporting

            if self.handler == "module_filter_warm":
                # Small, repeatedly used set — exercises the warm-cache path.
                file_names = tuple(f"errortracking_module_{index}.py" for index in range(16))
            else:
                file_names = tuple(f"errortracking_module_{index}.py" for index in range(8192))
            reporting.INSTRUMENTED_FILE_PATHS.clear()
            if hasattr(reporting, "_report_configured_modules"):
                reporting._report_configured_modules = True
                reporting._should_report_exception = None  # type: ignore[assignment]
                reporting._cached_should_report_exception.cache_clear()  # type: ignore[attr-defined]
            else:
                reporting._should_report_exception = reporting.create_should_report_exception_optimized({"modules"})
                getattr(reporting.cached_should_report_exception, "cache_clear")()

            if self.handler in ("module_filter_hit", "module_filter_warm"):
                paths: Any = reporting.INSTRUMENTED_FILE_PATHS
                if isinstance(paths, set):
                    paths.update(file_names)
                else:
                    paths.extend(file_names)

            if self.handler == "module_filter_warm":
                mask = 15  # 16 filenames
            else:
                mask = 8191  # 8192 filenames

            def _(loops: int) -> None:
                for index in range(loops):
                    reporting.cached_should_report_exception(file_names[index & mask])

            yield _
            return

        # -- production Error Tracking handler ---------------------------------

        if self.handler == "production_handler":
            # These settings are read when ddtrace is first imported below. Disable
            # trace export while retaining a real active span for the reporting path.
            os.environ["DD_ERROR_TRACKING_HANDLED_ERRORS"] = "all"
            os.environ["DD_TRACE_ENABLED"] = "false"

            from ddtrace import tracer
            from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

            HandledExceptionCollector.enable()
            span = tracer.trace("errortracking-monitoring-benchmark")

            def cleanup() -> None:
                try:
                    span.finish()
                finally:
                    HandledExceptionCollector.disable()

            # Fail the benchmark setup rather than silently measuring an inactive
            # collector if production registration or filtering stops working.
            try:
                try:
                    raise ValueError("benchmark setup")
                except ValueError:
                    pass
                if not HandledExceptionCollector.get_exception_events(span.span_id):
                    raise RuntimeError("production handled-exception callback did not report an event")
            except Exception:
                cleanup()
                raise

        # -- direct sys.monitoring (pre-multiplexer code path) -----------------

        elif self.handler == "direct_passive":
            sys_monitoring.use_tool_id(tool_id, "datadog_handled_exceptions")
            sys_monitoring.set_events(tool_id, event)

            def _direct_callback(code: CodeType, instruction_offset: int, exception: BaseException) -> None:
                pass

            sys_monitoring.register_callback(tool_id, event, _direct_callback)

            def cleanup() -> None:
                sys_monitoring.register_callback(tool_id, event, None)
                sys_monitoring.set_events(tool_id, 0)
                sys_monitoring.free_tool_id(tool_id)

        elif self.handler == "direct_active":
            seen_count = 0
            sys_monitoring.use_tool_id(tool_id, "datadog_handled_exceptions")
            sys_monitoring.set_events(tool_id, event)

            def _direct_callback(code: CodeType, instruction_offset: int, exception: BaseException) -> None:
                nonlocal seen_count
                seen_count += 1

            sys_monitoring.register_callback(tool_id, event, _direct_callback)

            def cleanup() -> None:
                sys_monitoring.register_callback(tool_id, event, None)
                sys_monitoring.set_events(tool_id, 0)
                sys_monitoring.free_tool_id(tool_id)

        # -- shared multiplexer (new code path) --------------------------------

        elif self.handler in ("direct_global_passive", "direct_global_active"):
            try:
                from ddtrace.internal import monitoring
            except ImportError:
                monitoring = None  # type: ignore[assignment]

            register_global = getattr(monitoring, "register_global", None)
            unregister_global = getattr(monitoring, "unregister_global", None)
            if register_global is None or unregister_global is None:
                # Use the equivalent direct callback as the pre-multiplexer baseline.
                sys_monitoring.use_tool_id(tool_id, "datadog_handled_exceptions")
                sys_monitoring.set_events(tool_id, event)

                if self.handler == "direct_global_active":
                    seen_direct_count = 0

                    def _direct_callback(code: CodeType, instruction_offset: int, exception: BaseException) -> None:
                        nonlocal seen_direct_count
                        seen_direct_count += 1
                else:

                    def _direct_callback(code: CodeType, instruction_offset: int, exception: BaseException) -> None:
                        pass

                sys_monitoring.register_callback(tool_id, event, _direct_callback)

                def cleanup() -> None:
                    sys_monitoring.register_callback(tool_id, event, None)
                    sys_monitoring.set_events(tool_id, 0)
                    sys_monitoring.free_tool_id(tool_id)

            else:
                if monitoring is None:
                    raise RuntimeError("monitoring direct registration unavailable")
                if self.handler == "direct_global_passive":

                    class _PassiveHandler(monitoring.MonitoringEventHandler):
                        def on_exception_handled(
                            self, code: CodeType, instruction_offset: int, exception: BaseException
                        ) -> None:
                            pass

                    handler: monitoring.MonitoringEventHandler = _PassiveHandler()
                else:

                    class _ActiveHandler(monitoring.MonitoringEventHandler):
                        def __init__(self) -> None:
                            self.seen_count = 0

                        def on_exception_handled(
                            self, code: CodeType, instruction_offset: int, exception: BaseException
                        ) -> None:
                            try:
                                self.seen_count += 1
                            except Exception:  # nosec B110 - benchmark code
                                pass

                    handler = _ActiveHandler()

                if "direct" in signature(register_global).parameters:
                    register_global(handler, direct=True)
                else:
                    register_global(handler)

                def cleanup() -> None:
                    unregister_global(handler)

        else:
            raise ValueError(f"Unknown handler config: {self.handler}")

        def _(loops: int) -> None:
            for _ in range(loops):
                try:
                    raise ValueError("bench")
                except ValueError:
                    pass

        try:
            yield _
        finally:
            if cleanup is not None:
                cleanup()
