"""Public logging handler for CI Visibility log correlation.

Ships log records to the Datadog logs intake, correlated with test traces.

Correlation IDs are read from record attributes ``dd.trace_id`` and
``dd.span_id`` at emit time.  Attach a :class:`CorrelationFilter` subclass
to set these attributes however suits your concurrency model (thread-local,
async, global, etc.).  A ready-made :class:`ThreadLocalCorrelationFilter`
is provided for the common case.

Example usage::

    import logging

    from ddtrace.testing.logs import DDTestLogsHandler
    from ddtrace.testing.logs import ThreadLocalCorrelationFilter

    handler = DDTestLogsHandler(service="my-service")
    correlation = ThreadLocalCorrelationFilter()
    handler.addFilter(correlation)
    logging.getLogger().addHandler(handler)

    while True:
        job = queue.get()
        correlation.set_context(trace_id=job.trace_id, span_id=job.span_id)
        run_test(job.item)

    handler.close()

Custom concurrency models
-------------------------
Subclass :class:`CorrelationFilter` and implement ``get_trace_id()`` /
``get_span_id()``.  The base class handles writing the values onto each
record under the ``dd.trace_id`` / ``dd.span_id`` attribute names.  How
the IDs are stored, and the signature of any setter, is up to the
subclass.  For asyncio, for example::

    import contextvars

    from ddtrace.testing.logs import CorrelationFilter

    class ContextVarCorrelationFilter(CorrelationFilter):
        def __init__(self):
            super().__init__()
            self._trace_id = contextvars.ContextVar("dd_trace_id", default=None)
            self._span_id = contextvars.ContextVar("dd_span_id", default=None)

        def set_context(self, trace_id, span_id):
            self._trace_id.set(trace_id)
            self._span_id.set(span_id)

        def get_trace_id(self):
            return self._trace_id.get()

        def get_span_id(self):
            return self._span_id.get()

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
import threading
from typing import Optional

from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.logs import LogsHandler
from ddtrace.testing.internal.logs import LogsWriter


class CorrelationFilter(logging.Filter):
    """Base class for filters that stamp ``dd.trace_id`` / ``dd.span_id`` onto log records.

    Subclasses implement :meth:`get_trace_id` and :meth:`get_span_id` to
    return the current correlation IDs from whatever storage their concurrency
    model uses.  The base class owns only the contract with
    :class:`DDTestLogsHandler`: it writes those IDs onto each record under
    the ``dd.trace_id`` / ``dd.span_id`` attribute names that the handler
    reads at emit time, defaulting to ``"0"`` when either getter returns
    ``None``.

    Setter shape (e.g. ``set_context``) is intentionally not defined on the
    base — different concurrency models need different signatures (sync vs.
    ``async def``, contextvars vs. thread-local, etc.).
    """

    def get_trace_id(self) -> Optional[str]:
        """Return the current trace ID, or ``None`` if no context is active."""
        raise NotImplementedError

    def get_span_id(self) -> Optional[str]:
        """Return the current span ID, or ``None`` if no context is active."""
        raise NotImplementedError

    def filter(self, record: logging.LogRecord) -> bool:
        setattr(record, "dd.trace_id", self.get_trace_id() or LOG_ATTR_VALUE_ZERO)
        setattr(record, "dd.span_id", self.get_span_id() or LOG_ATTR_VALUE_ZERO)
        return True


class ThreadLocalCorrelationFilter(CorrelationFilter):
    """:class:`CorrelationFilter` backed by :class:`threading.local` storage.

    Call :meth:`set_context` from the same thread that will emit the records;
    each thread sees its own ``(trace_id, span_id)``.  Threads that never call
    :meth:`set_context` produce records with the default ``"0"`` correlation
    IDs.
    """

    def __init__(self) -> None:
        super().__init__()
        self._local = threading.local()

    def set_context(self, trace_id: str, span_id: str) -> None:
        """Set the correlation IDs for the calling thread."""
        self._local.trace_id = trace_id
        self._local.span_id = span_id

    def clear_context(self) -> None:
        """Clear the correlation IDs for the calling thread."""
        self._local.__dict__.pop("trace_id", None)
        self._local.__dict__.pop("span_id", None)

    def get_trace_id(self) -> Optional[str]:
        return getattr(self._local, "trace_id", None)

    def get_span_id(self) -> Optional[str]:
        return getattr(self._local, "span_id", None)


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
