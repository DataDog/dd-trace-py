"""Microbenchmark for the shared ``sys.monitoring`` multiplexer.

Measures the overhead of the ``EXCEPTION_HANDLED`` global-handler path used by
error-tracking on Python 3.12+.  The benchmark exercises a tight try/except
loop so the ``on_exception_handled`` callback fires on every iteration.

Configurations (multiplexer vs. direct ``sys.monitoring``):

- ``direct_passive`` — raw ``sys.monitoring`` callback that does no work
- ``direct_active`` — raw ``sys.monitoring`` callback that records the exception
- ``multiplexer_passive`` — multiplexer ``register_global`` handler, no work
- ``multiplexer_active`` — multiplexer ``register_global`` handler, records exception

The ``direct_*`` configs reproduce the pre-multiplexer code path (a dedicated
tool slot with a single callback registered directly via
``sys.monitoring.register_callback``).  The ``multiplexer_*`` configs use the
shared multiplexer's ``register_global`` / ``unregister_global`` API.  Comparing
the two isolates the multiplexer dispatch overhead.

On branches where ``register_global`` is not yet available (e.g. ``main``), the
``multiplexer_*`` configs are skipped so the benchmark can still run the
``direct_*`` configs for baseline comparison.
"""

from collections.abc import Generator
from typing import Callable

import bm


class ErrorTrackingMonitoring(bm.Scenario):
    handler: str  # "direct_passive", "direct_active", "multiplexer_passive", "multiplexer_active"

    def run(self) -> Generator[Callable[[int], None], None]:
        import sys

        from ddtrace.internal import monitoring

        TOOL_ID = 3
        EVENT = sys.monitoring.events.EXCEPTION_HANDLED
        cleanup = None

        # -- direct sys.monitoring (pre-multiplexer code path) -----------------

        if self.handler == "direct_passive":
            sys.monitoring.use_tool_id(TOOL_ID, "datadog_handled_exceptions")
            sys.monitoring.set_events(TOOL_ID, EVENT)

            def _direct_callback(code, instruction_offset, exception):
                pass

            sys.monitoring.register_callback(TOOL_ID, EVENT, _direct_callback)

            def cleanup():
                sys.monitoring.register_callback(TOOL_ID, EVENT, None)
                sys.monitoring.set_events(TOOL_ID, 0)
                sys.monitoring.free_tool_id(TOOL_ID)

        elif self.handler == "direct_active":
            seen: list[BaseException] = []
            sys.monitoring.use_tool_id(TOOL_ID, "datadog_handled_exceptions")
            sys.monitoring.set_events(TOOL_ID, EVENT)

            def _direct_callback(code, instruction_offset, exception):
                seen.append(exception)

            sys.monitoring.register_callback(TOOL_ID, EVENT, _direct_callback)

            def cleanup():
                sys.monitoring.register_callback(TOOL_ID, EVENT, None)
                sys.monitoring.set_events(TOOL_ID, 0)
                sys.monitoring.free_tool_id(TOOL_ID)

        # -- shared multiplexer (new code path) --------------------------------

        elif self.handler in ("multiplexer_passive", "multiplexer_active"):
            if not hasattr(monitoring, "register_global"):
                # Baseline (main) does not have the multiplexer API yet.
                # Skip this config so the benchmark can still run direct_* configs.
                def _(loops: int) -> None:
                    pass

                yield _
                return

            if self.handler == "multiplexer_passive":

                class _Handler(monitoring.MonitoringEventHandler):
                    def on_exception_handled(self, code, instruction_offset, exception):
                        pass
            else:

                class _Handler(monitoring.MonitoringEventHandler):
                    def __init__(self):
                        self.seen: list[BaseException] = []

                    def on_exception_handled(self, code, instruction_offset, exception):
                        self.seen.append(exception)

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
