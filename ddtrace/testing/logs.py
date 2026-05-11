"""Public logging handler for CI Visibility log correlation.

Ships log records to the Datadog logs intake, correlated with test traces.

Correlation IDs are read from record attributes ``dd.trace_id`` and
``dd.span_id`` at emit time.  Attach a ``logging.Filter`` to set these
attributes however suits your concurrency model (thread-local, async,
global, etc.).

Example usage::

    import logging
    import threading

    from ddtrace.testing.logs import DDTestLogsHandler

    class CorrelationFilter(logging.Filter):
        def __init__(self):
            super().__init__()
            self._local = threading.local()

        def set_context(self, trace_id, span_id):
            self._local.trace_id = trace_id
            self._local.span_id = span_id

        def filter(self, record):
            setattr(record, "dd.trace_id", getattr(self._local, "trace_id", "0"))
            setattr(record, "dd.span_id", getattr(self._local, "span_id", "0"))
            return True

    handler = DDTestLogsHandler(service="my-service")
    correlation = CorrelationFilter()
    handler.addFilter(correlation)
    logging.getLogger().addHandler(handler)

    while True:
        job = queue.get()
        correlation.set_context(trace_id=job.trace_id, span_id=job.span_id)
        run_test(job.item)

    handler.close()
"""

from __future__ import annotations

from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.logs import LogsHandler
from ddtrace.testing.internal.logs import LogsWriter


class DDTestLogsHandler(LogsHandler):
    """Logging handler that ships records to the Datadog logs intake.

    Auto-detects the backend (agentless or EVP proxy) and manages the
    background writer lifecycle.  Call :meth:`close` explicitly or let
    Python's ``logging.shutdown()`` handle it at interpreter exit.
    """

    def __init__(self, service: str) -> None:
        connector_setup = BackendConnectorSetup.detect_setup()
        writer = LogsWriter(connector_setup, service=service)
        writer.start()
        super().__init__(writer)

    def close(self) -> None:
        self._writer.signal_finish()
        self._writer.wait_finish(timeout=30.0)
        super().close()
