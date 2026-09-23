"""Microbenchmark for the shared ``sys.monitoring`` multiplexer.

Measures the overhead of the global-handler dispatch path used by error-tracking
(``EXCEPTION_HANDLED``) and the exception profiler (``RAISE``) on Python 3.12+.

Configurations:

EXCEPTION_HANDLED (error-tracking path):
- ``direct_passive`` — raw ``sys.monitoring`` callback, no work
- ``direct_active`` — raw ``sys.monitoring`` callback, records exception
- ``multiplexer_passive`` — multiplexer handler, no work
- ``multiplexer_active`` — multiplexer handler, records exception

RAISE (exception profiler path):
- ``direct_raise_passive`` — raw ``sys.monitoring`` callback, no work (old profiler)
- ``direct_raise_active`` — raw ``sys.monitoring`` callback, records exception (old profiler)
- ``multiplexer_raise_passive`` — multiplexer handler, no work (new profiler)
- ``multiplexer_raise_active`` — multiplexer handler, records exception (new profiler)

The ``direct_*`` configs reproduce the pre-multiplexer code path (a dedicated
tool slot with a single callback registered directly via
``sys.monitoring.register_callback``).  The ``multiplexer_*`` configs use the
shared multiplexer's ``register_global`` / ``unregister_global`` API.

Comparing ``direct_raise_*`` vs ``multiplexer_raise_*`` isolates the multiplexer
overhead for the exception profiler migration.

On branches where ``register_global`` is not yet available (e.g. ``main``), the
``multiplexer_*`` configs are skipped so the benchmark can still run the
``direct_*`` configs for baseline comparison.
"""

from collections.abc import Generator
from typing import Callable

import bm


class SysMonitoring(bm.Scenario):
    handler: str

    def run(self) -> Generator[Callable[[int], None], None]:
        import sys

        from ddtrace.internal import monitoring

        TOOL_ID = 3
        EH_EVENT = sys.monitoring.events.EXCEPTION_HANDLED
        RAISE_EVENT = sys.monitoring.events.RAISE
        cleanup = None

        # -- direct sys.monitoring: EXCEPTION_HANDLED (old error-tracking path) -

        if self.handler == "direct_passive":
            sys.monitoring.use_tool_id(TOOL_ID, "datadog_handled_exceptions")
            sys.monitoring.set_events(TOOL_ID, EH_EVENT)

            def _cb(code, instruction_offset, exception):
                pass

            sys.monitoring.register_callback(TOOL_ID, EH_EVENT, _cb)

            def cleanup():
                sys.monitoring.register_callback(TOOL_ID, EH_EVENT, None)
                sys.monitoring.set_events(TOOL_ID, 0)
                sys.monitoring.free_tool_id(TOOL_ID)

        elif self.handler == "direct_active":
            seen: list[BaseException] = []
            sys.monitoring.use_tool_id(TOOL_ID, "datadog_handled_exceptions")
            sys.monitoring.set_events(TOOL_ID, EH_EVENT)

            def _cb(code, instruction_offset, exception):
                seen.append(exception)

            sys.monitoring.register_callback(TOOL_ID, EH_EVENT, _cb)

            def cleanup():
                sys.monitoring.register_callback(TOOL_ID, EH_EVENT, None)
                sys.monitoring.set_events(TOOL_ID, 0)
                sys.monitoring.free_tool_id(TOOL_ID)

        # -- direct sys.monitoring: RAISE (old exception profiler path) --------

        elif self.handler == "direct_raise_passive":
            sys.monitoring.use_tool_id(TOOL_ID, "dd-trace-exception-profiler")
            sys.monitoring.set_events(TOOL_ID, RAISE_EVENT)

            def _cb(code, instruction_offset, exception):
                pass

            sys.monitoring.register_callback(TOOL_ID, RAISE_EVENT, _cb)

            def cleanup():
                sys.monitoring.register_callback(TOOL_ID, RAISE_EVENT, None)
                sys.monitoring.set_events(TOOL_ID, 0)
                sys.monitoring.free_tool_id(TOOL_ID)

        elif self.handler == "direct_raise_active":
            seen_raise: list[BaseException] = []
            sys.monitoring.use_tool_id(TOOL_ID, "dd-trace-exception-profiler")
            sys.monitoring.set_events(TOOL_ID, RAISE_EVENT)

            def _cb(code, instruction_offset, exception):
                seen_raise.append(exception)

            sys.monitoring.register_callback(TOOL_ID, RAISE_EVENT, _cb)

            def cleanup():
                sys.monitoring.register_callback(TOOL_ID, RAISE_EVENT, None)
                sys.monitoring.set_events(TOOL_ID, 0)
                sys.monitoring.free_tool_id(TOOL_ID)

        # -- shared multiplexer (new code path) --------------------------------

        elif self.handler.startswith("multiplexer_"):
            if not hasattr(monitoring, "register_global"):
                # Baseline (main) does not have the multiplexer API yet.
                # Skip this config so the benchmark can still run direct_* configs.
                def _(loops: int) -> None:
                    pass

                yield _
                return

            # RAISE support was added after EXCEPTION_HANDLED; skip if the
            # baseline's _GLOBAL_EVENTS does not include RAISE.
            if self.handler.startswith("multiplexer_raise"):
                _RAISE_EVENT = sys.monitoring.events.RAISE
                if not (getattr(monitoring, "_GLOBAL_EVENTS", 0) & _RAISE_EVENT):

                    def _(loops: int) -> None:
                        pass

                    yield _
                    return

            if self.handler == "multiplexer_passive":

                class _Handler(monitoring.MonitoringEventHandler):
                    def on_exception_handled(self, code, instruction_offset, exception):
                        pass

            elif self.handler == "multiplexer_active":

                class _Handler(monitoring.MonitoringEventHandler):
                    def __init__(self):
                        self.seen: list[BaseException] = []

                    def on_exception_handled(self, code, instruction_offset, exception):
                        self.seen.append(exception)

            elif self.handler == "multiplexer_raise_passive":

                class _Handler(monitoring.MonitoringEventHandler):
                    def on_raise(self, code, instruction_offset, exception):
                        pass

            elif self.handler == "multiplexer_raise_active":

                class _Handler(monitoring.MonitoringEventHandler):
                    def __init__(self):
                        self.seen: list[BaseException] = []

                    def on_raise(self, code, instruction_offset, exception):
                        self.seen.append(exception)

            else:
                raise ValueError(f"Unknown handler config: {self.handler}")

            h = _Handler()
            monitoring.register_global(h)

            def cleanup():
                monitoring.unregister_global(h)

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
