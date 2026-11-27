"""Agentless submission of application logs to the Datadog logs intake.

Enabled with DD_AGENTLESS_LOG_SUBMISSION_ENABLED and DD_API_KEY: records that
propagate to the root logger are batched and POSTed to /api/v2/logs without
going through the Datadog Agent.
"""

from __future__ import annotations

import gzip
import json
import logging
from types import ModuleType
import typing as t
from urllib.parse import urlsplit
import weakref

from ddtrace import config
from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.constants import LOG_ATTR_SPAN_ID
from ddtrace.internal.constants import LOG_ATTR_TRACE_ID
from ddtrace.internal.constants import LOG_ATTR_VALUE_ZERO
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.http import verify_url


logger = get_logger(__name__)

_TRUNCATION_SUFFIX = "... [truncated]"


class LogsIntakeWriter(ForksafeAwakeablePeriodicService):
    """Buffers log events and periodically sends them to the logs intake.

    Requests respect the intake limits of 1000 entries and 5MB each; an event too large to fit
    in a request on its own is dropped. A batch that fails to send is dropped too: log
    submission is best-effort.
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
        _started_writers.add(self)
        _install_exit_flush()

    def flush_and_stop(self) -> None:
        """Send everything buffered at process exit, after any send already in progress."""
        try:
            self.stop()
        except ServiceStatusError:
            pass
        else:
            self.join(self._timeout)
        self.periodic()

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

    def _encode_packs(self, events: list[dict[str, t.Any]]) -> list[tuple[bytes, int]]:
        packs: list[list[bytes]] = []
        current: list[bytes] = []
        size = 2  # the enclosing brackets
        dropped = 0
        for event in events:
            # Plain UTF-8 rather than \uXXXX escapes, which take 6 bytes per non-ASCII character.
            encoded = json.dumps(event, ensure_ascii=False).encode("utf-8", "replace")
            if len(encoded) + 2 > self._MAX_REQUEST_BYTES:
                # Possible despite message truncation when JSON escapes expand it (control characters).
                dropped += 1
                continue
            if current and (
                len(current) >= self._MAX_EVENTS_PER_REQUEST or size + len(encoded) + 1 > self._MAX_REQUEST_BYTES
            ):
                packs.append(current)
                current, size = [], 2
            current.append(encoded)
            size += len(encoded) + 1
        if current:
            packs.append(current)
        if dropped:
            logger.warning("dropped %d log(s) larger than the %d byte request limit", dropped, self._MAX_REQUEST_BYTES)
        return [(b"[" + b",".join(pack) + b"]", len(pack)) for pack in packs]

    def _post(self, pack: bytes, count: int) -> None:
        conn = get_connection(self._intake_url, self._timeout)
        try:
            conn.request("POST", self._endpoint, gzip.compress(pack), self._headers)
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


_started_writers: weakref.WeakSet[LogsIntakeWriter] = weakref.WeakSet()
_exit_flush_installed = False


def _flush_started_writers() -> None:
    for writer in list(_started_writers):
        try:
            writer.flush_and_stop()
        except Exception:
            logger.debug("failed to flush logs at process exit", exc_info=True)


def _patch_process_bootstrap(process: ModuleType) -> None:
    bootstrap = process.BaseProcess._bootstrap

    def _bootstrap(process_self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:
        try:
            return bootstrap(process_self, *args, **kwargs)
        finally:
            # The bootstrap has run threading's shutdown, which joins the child's non-daemon
            # threads, so nothing is logged after this flush.
            _flush_started_writers()

    process.BaseProcess._bootstrap = _bootstrap


def _install_exit_flush() -> None:
    """Flush every started writer when the process exits normally.

    multiprocessing children started with fork or forkserver leave through os._exit() as soon as their
    bootstrap returns, skipping atexit; with module cloning the application imports its own copy of
    multiprocessing, hence the import hook. Nothing is flushed when a signal ends the process: a slow
    or unreachable intake must not delay its shutdown.
    """
    global _exit_flush_installed
    if _exit_flush_installed:
        return
    _exit_flush_installed = True
    ModuleWatchdog.register_module_hook("multiprocessing.process", _patch_process_bootstrap)
    atexit.register(_flush_started_writers)


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
            # A character takes at most 4 bytes in UTF-8, so only longer messages can exceed the limit.
            if len(message) > self._MAX_MESSAGE_BYTES // 4:
                encoded = message.encode("utf-8", "replace")
                if len(encoded) > self._MAX_MESSAGE_BYTES:
                    kept = encoded[: self._MAX_MESSAGE_BYTES - len(_TRUNCATION_SUFFIX)]
                    message = kept.decode("utf-8", "ignore") + _TRUNCATION_SUFFIX

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
    """Forwards application log records that propagate to the root logger to a LogsIntakeWriter."""

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


_handler: t.Optional[AgentlessLogsHandler] = None
_original_call_handlers = logging.Logger.callHandlers


def _call_handlers(self: logging.Logger, record: logging.LogRecord) -> None:
    _original_call_handlers(self, record)
    handler = _handler
    if handler is None:
        return
    # Submit what a root handler would receive, without being one: a root handler would
    # suppress logging.lastResort (the stderr output of apps with no logging configuration),
    # turn a later basicConfig() into a no-op, and be removed by dictConfig()/fileConfig().
    logger_ = self
    while logger_.parent is not None:
        if not logger_.propagate:
            return
        logger_ = logger_.parent
    handler.handle(record)


def enable_agentless_log_submission() -> bool:
    """Start submitting log records to the logs intake; return whether submission was enabled."""
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

    global _handler
    writer = LogsIntakeWriter(api_key=agentless_config.api_key, intake_url=intake_url)
    writer.start()
    _handler = AgentlessLogsHandler(writer)
    logging.Logger.callHandlers = _call_handlers  # type: ignore[method-assign]
    return True


def disable_agentless_log_submission() -> None:
    """Stop submitting log records and discard the ones not sent yet.

    The Test Optimization pytest plugin calls this so that test sessions submit logs only
    through its own handler, as they did before agentless log submission covered APM.
    """
    global _handler
    handler, _handler = _handler, None
    if handler is None:
        return
    if logging.Logger.callHandlers is _call_handlers:
        logging.Logger.callHandlers = _original_call_handlers  # type: ignore[method-assign]
    writer = handler._writer
    writer.reset()
    _started_writers.discard(writer)
    writer.stop()
