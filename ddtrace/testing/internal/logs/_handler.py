"""LogsHandler — logging.Handler that forwards records to the Datadog logs intake."""

from __future__ import annotations

import logging

from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.testing.internal.logs._writer import LogsWriter
from ddtrace.testing.internal.writer import Event


_log = logging.getLogger(__name__)


class LogsHandler(logging.Handler):
    """Logging handler that forwards records to the Datadog logs intake.

    Records are enriched with dd.trace_id/dd.span_id (present when the logging patch
    has been applied and a test span is active), then buffered in the LogsWriter for
    async delivery.
    """

    def __init__(self, writer: LogsWriter) -> None:
        super().__init__()
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        from ddtrace.testing.internal.logs import MAX_MESSAGE_BYTES
        from ddtrace.testing.internal.logs import TRUNCATION_SUFFIX

        # Respect the root logger's current level so that records from child loggers with a lower explicit
        # level don't bypass the level the user has configured globally.  Python's propagation skips the
        # parent logger's level check, so we have to enforce it here ourselves.
        root_level = logging.getLogger().level
        if root_level != logging.NOTSET and record.levelno < root_level:
            return

        try:
            message = record.getMessage()
            if len(message) > MAX_MESSAGE_BYTES:
                message = message[: MAX_MESSAGE_BYTES - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX

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
