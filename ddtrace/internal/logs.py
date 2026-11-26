"""Agentless submission of application logs to the Datadog logs intake.

Enabled with DD_AGENTLESS_LOG_SUBMISSION_ENABLED and DD_API_KEY: records that
reach the root logger's handlers are batched and POSTed to /api/v2/logs without
going through the Datadog Agent.
"""

from __future__ import annotations

import atexit
import gzip
import json
import logging
import typing as t
from urllib.parse import urlsplit

from ddtrace import config
from ddtrace.internal import forksafe
from ddtrace.internal.constants import LOG_ATTR_SPAN_ID
from ddtrace.internal.constants import LOG_ATTR_TRACE_ID
from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.http import verify_url


logger = get_logger(__name__)

_TRUNCATION_SUFFIX = "... [truncated]"


class LogsIntakeWriter(ForksafeAwakeablePeriodicService):
    """Buffers log events and periodically sends them to the logs intake.

    Requests respect the intake limits of 1000 entries and 5MB each.
    """

    _MAX_EVENTS_PER_REQUEST = 1000
    _MAX_REQUEST_BYTES = 5 * 1024 * 1024
    _MAX_BUFFER_EVENTS = 10_000

    def __init__(self, api_key: str, intake_url: str, interval: float = 5.0, timeout: float = 10.0) -> None:
        super().__init__(interval=interval)
        # Application threads enqueue under this lock, so it must be reset on fork: a
        # thread holding it when another one forks would otherwise deadlock the child.
        self._lock = forksafe.RLock()
        self._buffer: list[dict[str, t.Any]] = []
        self._timeout = timeout
        self._intake_url = intake_url
        # get_connection() keeps only scheme, host and port, so carry any path prefix in the endpoint.
        self._endpoint = urlsplit(intake_url).path.rstrip("/") + "/api/v2/logs"
        self._url = intake_url.rstrip("/") + "/api/v2/logs"
        self._headers = {"DD-API-KEY": api_key, "Content-Type": "application/json", "Content-Encoding": "gzip"}
        logger.debug("started log writer to %r", self._url)

    def start(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().start(*args, **kwargs)
        atexit.register(self.periodic)

    def enqueue(self, event: dict[str, t.Any]) -> None:
        with self._lock:
            if len(self._buffer) >= self._MAX_BUFFER_EVENTS:
                logger.warning("log buffer full (limit is %d), dropping log", self._MAX_BUFFER_EVENTS)
                return
            self._buffer.append(event)

    def reset(self) -> None:
        # The child inherits the parent's buffer; the parent submits those events.
        with self._lock:
            self._buffer = []

    def periodic(self) -> None:
        with self._lock:
            events, self._buffer = self._buffer, []
        for pack, count in self._encode_packs(events):
            self._post(pack, count)

    def _encode_packs(self, events: list[dict[str, t.Any]]) -> list[tuple[str, int]]:
        packs: list[list[str]] = []
        current: list[str] = []
        size = 2  # the enclosing brackets
        for event in events:
            # ASCII-only output (the json default), so the length is the size in bytes.
            encoded = json.dumps(event)
            if current and (
                len(current) >= self._MAX_EVENTS_PER_REQUEST or size + len(encoded) + 1 > self._MAX_REQUEST_BYTES
            ):
                packs.append(current)
                current, size = [], 2
            current.append(encoded)
            size += len(encoded) + 1
        if current:
            packs.append(current)
        return [("[%s]" % ",".join(pack), len(pack)) for pack in packs]

    def _connect(self) -> t.Any:
        return get_connection(self._intake_url, self._timeout)

    def _post(self, pack: str, count: int) -> None:
        conn = self._connect()
        try:
            conn.request("POST", self._endpoint, gzip.compress(pack.encode("utf-8")), self._headers)
            resp = conn.getresponse()
            if resp.status >= 300:
                logger.error(
                    "failed to send %d logs to %r, got response code %r, status: %r",
                    count,
                    self._url,
                    resp.status,
                    resp.read(),
                    extra={"send_to_telemetry": False},
                )
            else:
                logger.debug("sent %d logs to %r", count, self._url)
        except Exception:
            logger.error(
                "failed to send %d logs to %r", count, self._url, exc_info=True, extra={"send_to_telemetry": False}
            )
        finally:
            conn.close()


class LogsIntakeHandler(logging.Handler):
    """Turns log records into logs intake events; subclasses decide where _submit() sends them."""

    # Larger entries are accepted and truncated by the intake, but they must still fit in a request.
    _MAX_MESSAGE_BYTES = 1024 * 1024

    # A fixed formatter keeps the message free of any layout set with setFormatter(); it
    # only appends the traceback when the record carries exc_info.
    _FORMATTER = logging.Formatter()

    def __init__(self, service: str, ddtags: str, hostname: str) -> None:
        super().__init__()
        self._service = service
        self._ddtags = ddtags
        self._hostname = hostname

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self._FORMATTER.format(record)
            if len(message) > self._MAX_MESSAGE_BYTES:
                message = message[: self._MAX_MESSAGE_BYTES - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

            self._submit(
                {
                    "date": int(record.created * 1000),
                    "ddsource": "python",
                    "ddtags": self._ddtags,
                    "hostname": self._hostname,
                    "message": message,
                    "service": self._service,
                    "status": record.levelname.lower(),
                    # Injected by the logging integration when the record is created (DD_LOGS_INJECTION).
                    LOG_ATTR_TRACE_ID: getattr(record, LOG_ATTR_TRACE_ID, LOG_ATTR_VALUE_ZERO),
                    LOG_ATTR_SPAN_ID: getattr(record, LOG_ATTR_SPAN_ID, LOG_ATTR_VALUE_ZERO),
                }
            )
        except Exception:
            self.handleError(record)

    def _submit(self, event: dict[str, t.Any]) -> None:
        raise NotImplementedError


class AgentlessLogsHandler(LogsIntakeHandler):
    """Root logger handler that forwards application log records to a LogsIntakeWriter."""

    def __init__(self, writer: LogsIntakeWriter) -> None:
        tags = dict(config.tags)
        if config.env:
            tags["env"] = config.env
        if config.version:
            tags["version"] = config.version
        super().__init__(
            service=config.service or "",
            ddtags=",".join("%s:%s" % (key, value) for key, value in tags.items()),
            hostname=get_hostname(),
        )
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        # Submitting ddtrace's own logs could feed back into themselves (e.g. send failures).
        if record.name == "ddtrace" or record.name.startswith("ddtrace."):
            return
        super().emit(record)

    def _submit(self, event: dict[str, t.Any]) -> None:
        self._writer.enqueue(event)


def enable_agentless_log_submission() -> bool:
    """Install the agentless log handler on the root logger; return whether it was installed."""
    if config._otel_logs_enabled:
        logger.warning(
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED is ignored because DD_LOGS_OTEL_ENABLED is set; "
            "logs are exported through OpenTelemetry instead."
        )
        return False
    if not agentless_config.api_key:
        logger.warning(
            "DD_AGENTLESS_LOG_SUBMISSION_ENABLED is set but DD_API_KEY is not set; logs will not be submitted."
        )
        return False

    intake_url = agentless_config.log_submission_url or "https://http-intake.logs.%s" % agentless_config.site
    try:
        verify_url(intake_url)
    except ValueError:
        logger.warning("Invalid logs intake URL %r; logs will not be submitted.", intake_url, exc_info=True)
        return False

    writer = LogsIntakeWriter(api_key=agentless_config.api_key, intake_url=intake_url)
    writer.start()
    logging.getLogger().addHandler(AgentlessLogsHandler(writer))
    return True
