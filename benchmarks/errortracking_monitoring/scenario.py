"""Microbenchmark for the shared ``sys.monitoring`` multiplexer.

Measures the overhead of the ``EXCEPTION_HANDLED`` global-handler path used by
error-tracking on Python 3.12+.  The benchmark exercises a tight try/except
loop so the ``on_exception_handled`` callback fires on every iteration.

Configurations:

- ``baseline`` — no monitoring registered (raw try/except cost)
- ``no_handler`` — multiplexer tool claimed, no global handler registered
- ``passive_handler`` — global handler that does no work (measures dispatch cost)
- ``active_handler`` — global handler that records the exception (realistic path)
"""

from collections.abc import Generator
from typing import Callable

import bm


class ErrorTrackingMonitoring(bm.Scenario):
    handler: str  # "none", "passive", "active"

    def run(self) -> Generator[Callable[[int], None], None]:
        from ddtrace.internal import monitoring

        class _PassiveHandler(monitoring.MonitoringEventHandler):
            def on_exception_handled(self, code, instruction_offset, exception):
                pass

        class _ActiveHandler(monitoring.MonitoringEventHandler):
            def __init__(self):
                self.seen: list[BaseException] = []

            def on_exception_handled(self, code, instruction_offset, exception):
                self.seen.append(exception)

        cleanup = None

        if self.handler == "none":
            # Claim the tool but register no global handler — measures the
            # EXCEPTION_HANDLED callback dispatch with an empty snapshot.
            monitoring.get_tool_id()

            def cleanup():
                monitoring._release_tool_if_unused()

        elif self.handler == "passive":
            h = _PassiveHandler()
            monitoring.register_global(h)

            def cleanup():
                monitoring.unregister_global(h)

        elif self.handler == "active":
            h = _ActiveHandler()
            monitoring.register_global(h)

            def cleanup():
                monitoring.unregister_global(h)

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
