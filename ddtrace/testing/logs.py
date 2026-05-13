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

Configuration
-------------
The handler reads the following standard environment variables:

Agentless mode (set ``DD_CIVISIBILITY_AGENTLESS_ENABLED=true``):
    - ``DD_API_KEY``  — Datadog API key (required)
    - ``DD_SITE``     — Datadog site (default: ``datadoghq.com``)

Agent/EVP proxy mode (default):
    - ``DD_TRACE_AGENT_URL``       — full agent URL (e.g. ``http://localhost:8126``)
    - ``DD_TRACE_AGENT_HOSTNAME``  — agent hostname (default: ``localhost``)
    - ``DD_AGENT_HOST``            — alias for ``DD_TRACE_AGENT_HOSTNAME``
    - ``DD_TRACE_AGENT_PORT``      — agent port (default: ``8126``)
    - ``DD_AGENT_PORT``            — alias for ``DD_TRACE_AGENT_PORT``
"""

from __future__ import annotations

import logging

from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.logs import LogsHandler
from ddtrace.testing.internal.logs import LogsWriter


class DDTestLogsHandler(LogsHandler):
    """Logging handler that ships records to the Datadog logs intake.

    Raises :exc:`~ddtrace.testing.internal.errors.SetupError` at construction
    if the required environment variables are missing or the agent is unreachable.

    Call :meth:`close` explicitly or use as a context manager to flush
    buffered records before the process exits.
    """

    def __init__(self, service: str, *, shutdown_timeout: float = 5.0) -> None:
        connector_setup = BackendConnectorSetup.detect_standard_setup()
        writer = LogsWriter(connector_setup, service=service)
        writer.start()
        super().__init__(writer)
        self._shutdown_timeout = shutdown_timeout
        self._closed = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        super().emit(record)

    def close(self) -> None:
        # Gate new emits out before signalling the writer so that records
        # accepted before close() cannot queue into a writer that will never flush.
        self._closed = True
        self._writer.signal_finish()
        self._writer.wait_finish(timeout=self._shutdown_timeout)
        super().close()

    def __enter__(self) -> "DDTestLogsHandler":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
