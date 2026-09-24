"""Microbenchmark for the shared ``sys.monitoring`` multiplexer.

Measures the overhead of the ``EXCEPTION_HANDLED`` global-handler path used by
error-tracking on Python 3.12+.  The benchmark exercises a tight try/except
loop so the ``on_exception_handled`` callback fires on every iteration.

Configurations (multiplexer vs. direct ``sys.monitoring``):

- ``direct_passive`` — raw ``sys.monitoring`` callback that does no work
- ``direct_active`` — raw ``sys.monitoring`` callback that records the exception
- ``multiplexer_passive`` — multiplexer ``register_global`` handler, no work
- ``multiplexer_active`` — multiplexer ``register_global`` handler, records exception
- ``module_filter_miss`` — module-only filtering with high-cardinality rejected filenames
- ``module_filter_hit`` — module-only filtering with configured filenames

The ``direct_*`` configs reproduce the pre-multiplexer code path (a dedicated
tool slot with a single callback registered directly via
``sys.monitoring.register_callback``).  The ``multiplexer_*`` configs use the
shared multiplexer's ``register_global`` / ``unregister_global`` API, opting
into direct delivery when supported. Comparing the two isolates dispatch
overhead while keeping tool ownership centralized.

On branches where ``register_global`` is not yet available, the
``multiplexer_*`` configurations use the equivalent direct callback as their
baseline so comparison output remains meaningful.
"""

from collections.abc import Generator
from inspect import signature
from types import CodeType
from typing import Any
from typing import Callable

import bm


class ErrorTrackingMonitoring(bm.Scenario):  # type: ignore[misc]
    handler: str

    def run(self) -> Generator[Callable[[int], None], None]:
        import sys

        from ddtrace.internal import monitoring

        sys_monitoring = getattr(sys, "monitoring")
        tool_id = 3
        event = sys_monitoring.events.EXCEPTION_HANDLED
        cleanup: Callable[[], None] | None = None

        # -- module filename filtering -----------------------------------------

        if self.handler in ("module_filter_miss", "module_filter_hit"):
            from ddtrace.errortracking._handled_exceptions import monitoring_reporting as reporting

            file_names = tuple(f"errortracking_module_{index}.py" for index in range(8192))
            reporting.INSTRUMENTED_FILE_PATHS.clear()
            if hasattr(reporting, "_report_configured_modules"):
                reporting._report_configured_modules = True
                reporting._should_report_exception = None
                reporting._cached_should_report_exception.cache_clear()
            else:
                reporting._should_report_exception = reporting.create_should_report_exception_optimized({"modules"})
                getattr(reporting.cached_should_report_exception, "cache_clear")()

            if self.handler == "module_filter_hit":
                paths: Any = reporting.INSTRUMENTED_FILE_PATHS
                if isinstance(paths, set):
                    paths.update(file_names)
                else:
                    paths.extend(file_names)

            def _(loops: int) -> None:
                for index in range(loops):
                    reporting.cached_should_report_exception(file_names[index & 8191])

            yield _
            return

        # -- direct sys.monitoring (pre-multiplexer code path) -----------------

        if self.handler == "direct_passive":
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
            seen: list[BaseException] = []
            sys_monitoring.use_tool_id(tool_id, "datadog_handled_exceptions")
            sys_monitoring.set_events(tool_id, event)

            def _direct_callback(code: CodeType, instruction_offset: int, exception: BaseException) -> None:
                seen.append(exception)

            sys_monitoring.register_callback(tool_id, event, _direct_callback)

            def cleanup() -> None:
                sys_monitoring.register_callback(tool_id, event, None)
                sys_monitoring.set_events(tool_id, 0)
                sys_monitoring.free_tool_id(tool_id)

        # -- shared multiplexer (new code path) --------------------------------

        elif self.handler in ("multiplexer_passive", "multiplexer_active"):
            register_global = getattr(monitoring, "register_global", None)
            unregister_global = getattr(monitoring, "unregister_global", None)
            if register_global is None or unregister_global is None:
                # Use the equivalent direct callback as the pre-multiplexer baseline.
                seen = []
                sys_monitoring.use_tool_id(tool_id, "datadog_handled_exceptions")
                sys_monitoring.set_events(tool_id, event)

                def _direct_callback(code: CodeType, instruction_offset: int, exception: BaseException) -> None:
                    if self.handler == "multiplexer_active":
                        seen.append(exception)

                sys_monitoring.register_callback(tool_id, event, _direct_callback)

                def cleanup() -> None:
                    sys_monitoring.register_callback(tool_id, event, None)
                    sys_monitoring.set_events(tool_id, 0)
                    sys_monitoring.free_tool_id(tool_id)

            else:
                if self.handler == "multiplexer_passive":

                    class _PassiveHandler(monitoring.MonitoringEventHandler):
                        def on_exception_handled(
                            self, code: CodeType, instruction_offset: int, exception: BaseException
                        ) -> None:
                            pass

                    handler: monitoring.MonitoringEventHandler = _PassiveHandler()
                else:

                    class _ActiveHandler(monitoring.MonitoringEventHandler):
                        def __init__(self) -> None:
                            self.seen: list[BaseException] = []

                        def on_exception_handled(
                            self, code: CodeType, instruction_offset: int, exception: BaseException
                        ) -> None:
                            try:
                                self.seen.append(exception)
                            except Exception:
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
