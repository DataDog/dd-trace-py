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


class LogsWriter(BaseWriter):
    """Sends batches of log events to the Datadog logs intake as JSON."""

    def __init__(self, connector_setup: BackendConnectorSetup, service: str) -> None:
        super().__init__()
        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.LOGS)
        self.service = service
        self.hostname = socket.gethostname()

    def _encode_events(self, events: list[Event]) -> bytes:
        return json.dumps(events).encode("utf-8")

    def _send_events(self, events: list[Event]) -> bool:
        packs = self._split_pack_events(events)
        for pack in packs:
            result = self.connector.request(
                "POST",
                "/api/v2/logs",
                data=pack,
                headers={"Content-Type": "application/json"},
                send_gzip=True,
            )
            self.connector.close()
            if result.error_type:
                _log.warning("Failed to submit logs to Datadog logs intake: %s", result.error_description)
                return False
            else:
                _log.debug("Submitted %d log event(s) to Datadog logs intake", len(events))
        return True


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
        # Respect the root logger's current level so that records from child loggers with a lower explicit
        # level don't bypass the level the user has configured globally.  Python's propagation skips the
        # parent logger's level check, so we have to enforce it here ourselves.
        root_level = logging.getLogger().level
        if root_level != logging.NOTSET and record.levelno < root_level:
            return

        try:
            event: Event = {
                "ddsource": "python",
                "ddtags": "datadog.product:citest",
                "hostname": self._writer.hostname,
                "message": record.getMessage(),
                "service": self._writer.service,
                "status": record.levelname.lower(),
                "dd.trace_id": getattr(record, "dd.trace_id", LOG_ATTR_VALUE_ZERO),
                "dd.span_id": getattr(record, "dd.span_id", LOG_ATTR_VALUE_ZERO),
            }
            self._writer.put_event(event)
        except Exception:
            self.handleError(record)
