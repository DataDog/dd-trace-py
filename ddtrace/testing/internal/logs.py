"""Agentless log submission for test sessions.

When DD_AGENTLESS_LOG_SUBMISSION_ENABLED=true (alongside DD_CIVISIBILITY_AGENTLESS_ENABLED=true),
log records emitted during tests are forwarded directly to the Datadog logs intake at
https://http-intake.logs.<DD_SITE>/api/v2/logs.

Each submitted record is enriched with dd.trace_id/dd.span_id (injected by the logging patch)
so logs appear correlated on the Test Run page in Test Optimization.
"""

from __future__ import annotations

import json
import logging
import socket

from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import Subdomain
from ddtrace.testing.internal.writer import BaseWriter
from ddtrace.testing.internal.writer import Event


_log = logging.getLogger(__name__)

_TRUNCATION_SUFFIX = "... [truncated]"


class LogsWriter(BaseWriter):
    """Sends batches of log events to the Datadog logs intake as JSON."""

    # Cap the in-memory buffer to avoid OOM when tests produce very large volumes of logs.
    # Events beyond this limit are dropped with a warning.
    _MAX_BUFFER_EVENTS = 10_000

    # Flush more aggressively than the default 60 s so that the buffer drains continuously
    # during long-running tests and the teardown flush has less work to do.
    _FLUSH_INTERVAL_SECONDS = 5

    # The Datadog logs intake accepts at most 1000 entries per POST request.
    # See https://docs.datadoghq.com/api/latest/logs/#send-logs
    _MAX_EVENTS_PER_REQUEST = 1000

    def __init__(self, connector_setup: BackendConnectorSetup, service: str) -> None:
        super().__init__(max_buffer_events=self._MAX_BUFFER_EVENTS)
        self.flush_interval_seconds = self._FLUSH_INTERVAL_SECONDS
        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.LOGS)
        self.service = service
        self.hostname = socket.gethostname()

    def _encode_events(self, events: list[Event]) -> bytes:
        return json.dumps(events).encode("utf-8")

    def _send_events(self, events: list[Event]) -> bool:
        # Split into chunks of at most _MAX_EVENTS_PER_REQUEST, then further
        # split each chunk by byte size via _split_pack_events.
        for offset in range(0, len(events), self._MAX_EVENTS_PER_REQUEST):
            chunk = events[offset : offset + self._MAX_EVENTS_PER_REQUEST]
            packs = self._split_pack_events(chunk)
            for pack in packs:
                result = self.connector.request(
                    "POST",
                    "/api/v2/logs",
                    data=pack,
                    headers={"Content-Type": "application/json"},
                    send_gzip=True,
                )
                if result.error_type:
                    _log.warning("Failed to submit logs to Datadog logs intake: %s", result.error_description)
                    return False
            _log.debug("Submitted %d log event(s) to Datadog logs intake", len(chunk))
        return True


class LogsHandler(logging.Handler):
    """Logging handler that forwards records to the Datadog logs intake.

    Records are enriched with dd.trace_id/dd.span_id (present when the logging patch
    has been applied and a test span is active), then buffered in the LogsWriter for
    async delivery.
    """

    # Truncate individual log messages at the Datadog logs intake per-entry limit of 1 MB.
    _MAX_MESSAGE_BYTES = 1 * 1024 * 1024  # 1 MB

    def __init__(self, writer: LogsWriter) -> None:
        super().__init__()
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        # Respect the root logger's current level so that records from child loggers with a lower explicit
        # level don't bypass the level the user has configured globally.  Python's propagation skips the
        # parent logger's level check, so we have to enforce it here ourselves.
        root_level = logging.getLogger().level
        if root_level != logging.NOTSET and record.levelno < root_level:
            return

        try:
            message = record.getMessage()
            if len(message) > self._MAX_MESSAGE_BYTES:
                message = message[: self._MAX_MESSAGE_BYTES - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

            # Use the log record's creation time rather than the current time so that
            # events buffered before a flush carry an accurate timestamp.
            timestamp_ms = int(record.created * 1000)

            event: Event = {
                "date": timestamp_ms,
                "ddsource": "python",
                "ddtags": "datadog.product:citest",
                "hostname": self._writer.hostname,
                "message": message,
                "service": self._writer.service,
                "status": record.levelname.lower(),
                "dd.trace_id": getattr(record, "dd.trace_id", LOG_ATTR_VALUE_ZERO),
                "dd.span_id": getattr(record, "dd.span_id", LOG_ATTR_VALUE_ZERO),
            }
            self._writer.put_event(event)
        except Exception:
            self.handleError(record)
