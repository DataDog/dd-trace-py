"""LogsWriter — async batched writer for the Datadog logs intake."""

from __future__ import annotations

import json
import logging
import socket

from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import Subdomain
from ddtrace.testing.internal.writer import BaseWriter
from ddtrace.testing.internal.writer import Event


_log = logging.getLogger(__name__)


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
