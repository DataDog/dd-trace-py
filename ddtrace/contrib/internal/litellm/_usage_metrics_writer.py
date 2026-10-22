"""Periodic export of LiteLLM usage and cost metrics.

Records are projected into metric points by the native ai_usage module as they arrive. Every
interval the points recorded since the last flush are encoded once and sent, either as an OTLP/HTTP
protobuf ExportMetricsServiceRequest (delta temporality) or as DogStatsD lines.
"""

from __future__ import annotations

from collections import deque
import datetime
import email.utils
import errno
import random
import socket
import time
from typing import Any
from typing import Optional
from urllib import parse

from ddtrace.internal import forksafe
from ddtrace.internal.http_client import HTTPClient
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import TimedOutError
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.telemetry import get_config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.version import __version__


try:
    from ddtrace.internal.native import ai_usage
except ImportError:
    # Builds without the native ai_usage module (serverless) cannot record these metrics.
    ai_usage = None  # type: ignore[misc,assignment]


log = get_logger(__name__)

SCOPE_NAME = "ddtrace.contrib.litellm"

# Largest datagram sent to DogStatsD over UDP and over a Unix socket.
_UDP_MAX_PACKET = 1432
_UDS_MAX_PACKET = 8192
# The Agent reads a DogStatsD datagram into an 8 KiB buffer by default, so a longer line would arrive cut.
_MAX_LINE = 8192
_SEND_TIMEOUT_SECONDS = 0.1
_SEND_ATTEMPTS = 20

# The OTLP/HTTP responses a client retries; any other failure status drops the export.
_RETRYABLE_STATUSES = frozenset((429, 502, 503, 504))
# Backoff before each retry within one flush, before jitter. An export that still fails waits for the next flush.
_RETRY_DELAYS_SECONDS = (0.5, 1.0)
_MAX_RETRY_DELAY_SECONDS = 5.0
# Exports kept for a later flush while the endpoint is unavailable; the oldest is dropped first.
_MAX_PENDING_EXPORTS = 8
# How long the last flush at exit may spend sending what is left.
_SHUTDOWN_SECONDS = 5.0


def _wait(stopping: Any, seconds: float) -> bool:
    """Wait before a retry. Return True early when the writer is stopping."""
    return bool(stopping.wait(seconds))


def _setting(name: str) -> Optional[str]:
    """A setting from fleet or local stable configuration or the environment, in their usual precedence. Each name is
    looked up on its own, so a metrics setting from any source wins over the global one.
    """
    value = get_config(name, None, report_telemetry=False)
    return str(value) if value else None


def otlp_endpoint_is_grpc() -> bool:
    """Whether the configured OTLP metrics endpoint is a gRPC one: its protocol, the metrics one or else the global
    one, is gRPC. The usage metrics are sent over OTLP/HTTP only, which a gRPC endpoint does not accept. Without a
    configured endpoint they go to an OTLP/HTTP default.
    """
    if not (_setting("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or _setting("OTEL_EXPORTER_OTLP_ENDPOINT")):
        return False
    protocol = _setting("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL") or _setting("OTEL_EXPORTER_OTLP_PROTOCOL") or ""
    return protocol.strip().lower() == "grpc"


def _redacted(url: str) -> str:
    """The URL's scheme, host and port, for logs: its credentials, path and query can all hold secrets."""
    parts = parse.urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    return parse.urlunsplit((parts.scheme, host, "", "", ""))


def _parse_headers(raw: str) -> list[tuple[str, str]]:
    headers = []
    for item in (raw or "").split(","):
        key, sep, value = item.strip().partition("=")
        if sep and key.strip():
            headers.append((key.strip(), parse.unquote(value.strip())))
    return headers


def _retry_after(value: Optional[str]) -> Optional[float]:
    """The delay a Retry-After header asks for, in seconds, from its delay-seconds or its HTTP-date form."""
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        return None
    return max((when - datetime.datetime.now(datetime.timezone.utc)).total_seconds(), 0.0)


def _varint(data: bytes, position: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7


def _fields(data: bytes) -> Any:
    """The (field number, value) pairs of a protobuf message: an int for a varint, bytes for a length-delimited
    field. Other wire types end the walk.
    """
    position = 0
    while position < len(data):
        key, position = _varint(data, position)
        if key & 7 == 0:
            value, position = _varint(data, position)
            yield key >> 3, value
        elif key & 7 == 2:
            length, position = _varint(data, position)
            yield key >> 3, data[position : position + length]
            position += length
        else:
            return


def _partial_success(body: bytes) -> Optional[tuple[int, str]]:
    """The rejected point count and message of an ExportMetricsServiceResponse's partial_success, if it states one."""
    try:
        for number, value in _fields(body):
            if number == 1 and isinstance(value, bytes):
                rejected, message = 0, ""
                for inner, inner_value in _fields(value):
                    if inner == 1 and isinstance(inner_value, int):
                        rejected = inner_value
                    elif inner == 2 and isinstance(inner_value, bytes):
                        message = inner_value.decode("utf-8", errors="replace")
                return (rejected, message) if rejected or message else None
    except (IndexError, TypeError, ValueError):
        pass
    return None


def _timeout_ms() -> int:
    """OTEL_EXPORTER_OTLP_METRICS_TIMEOUT, falling back to OTEL_EXPORTER_OTLP_TIMEOUT as the endpoint and headers do.
    ddtrace's own settings already report both to telemetry. A value that is not a positive whole number of
    milliseconds gives the default.
    """
    default_ms = int(otel_config.exporter.DEFAULT_TIMEOUT)
    value = _setting("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT") or _setting("OTEL_EXPORTER_OTLP_TIMEOUT")
    if value is None:
        return default_ms
    try:
        timeout_ms = int(value)
    except (TypeError, ValueError):
        timeout_ms = 0
    if timeout_ms <= 0:
        log.warning(
            "Ignoring the OTLP metrics timeout %r, which is not a positive number of milliseconds; using %s ms",
            value,
            default_ms,
        )
        return default_ms
    return timeout_ms


class _OtlpSender:
    def __init__(self) -> None:
        # OTEL_EXPORTER_OTLP_METRICS_ENDPOINT is used exactly as given, path and query included; otherwise
        # this is OTEL_EXPORTER_OTLP_ENDPOINT with /v1/metrics appended to its path (its query kept), the agentless
        # intake, or the local Agent.
        endpoint = _setting("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
        # Whether the export goes to the local Agent's OTLP receiver, which the Agent does not enable by default.
        self._to_agent = False
        headers = _parse_headers(
            _setting("OTEL_EXPORTER_OTLP_METRICS_HEADERS") or _setting("OTEL_EXPORTER_OTLP_HEADERS") or ""
        )
        if endpoint is None:
            base = _setting("OTEL_EXPORTER_OTLP_ENDPOINT")
            if base:
                parts = parse.urlsplit(base)
                endpoint = parse.urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/v1/metrics"))
            else:
                endpoint = otel_config.exporter.TRACE_METRICS_ENDPOINT
                self._to_agent = not agentless_config.enabled
                # The API key goes only to the Datadog intake, the default when no endpoint is configured anywhere.
                api_key = agentless_config.api_key
                if agentless_config.enabled and api_key and all(name.lower() != "dd-api-key" for name, _ in headers):
                    headers.append(("dd-api-key", api_key))
        url = parse.urlsplit(endpoint)
        self.path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        self.endpoint = _redacted(endpoint)
        self._timeout_ms = _timeout_ms()
        self._client = HTTPClient(
            f"{url.scheme}://{url.netloc}",
            timeout_ms=self._timeout_ms,
            headers=headers,
            treat_http_errors_as_errors=False,
        )

    def send(self, payload: bytes, stopping: Any, retry: bool = True, deadline: Optional[float] = None) -> bool:
        """Send one export, retrying a retryable failure after a short backoff, unless stopping is set. A request
        does not outlast deadline, a time.monotonic() value.

        Return False when the export still failed in a way worth trying again at the next flush: a retryable
        status, a refused connection, a timeout, or a broken response. Any other failure drops it.
        """
        delays = _RETRY_DELAYS_SECONDS if retry else ()
        failure = ""
        for attempt in range(len(delays) + 1):
            requested: Optional[float] = None
            timeout_ms = None
            if deadline is not None:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                timeout_ms = max(min(remaining_ms, self._timeout_ms), 1)
            try:
                response = self._client.post(
                    self.path,
                    headers=[("Content-Type", "application/x-protobuf")],
                    body=payload,
                    timeout_ms=timeout_ms,
                )
            except (ConnectionFailedError, TimedOutError, HttpIoError) as e:
                failure = type(e).__name__
            else:
                status = response.status_code
                if 200 <= status < 300:
                    # A partial success is not retried: the endpoint has kept what it accepted.
                    partial = _partial_success(response.body())
                    if partial is not None:
                        log.warning(
                            "LiteLLM usage metrics: the OTLP endpoint at %s rejected %d data points: %s",
                            self.endpoint,
                            partial[0],
                            partial[1][:200],
                        )
                    return True
                if status not in _RETRYABLE_STATUSES:
                    log.warning("LiteLLM usage metrics: OTLP export to %s failed with status %s", self.endpoint, status)
                    return True
                failure = f"status {status}"
                requested = _retry_after(response.header("Retry-After"))
            if attempt < len(delays):
                delay = requested if requested is not None else delays[attempt] * random.uniform(0.5, 1.0)  # nosec B311
                if _wait(stopping, min(delay, _MAX_RETRY_DELAY_SECONDS)):
                    break
        # Warnings are rate-limited per call site. Without them, an Agent whose OTLP receiver is off loses every metric
        # silently.
        if self._to_agent:
            log.warning(
                "LiteLLM usage metrics: OTLP export to the Datadog Agent at %s failed (%s), kept for the next flush. "
                "Enable the Agent's OTLP receiver (DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_HTTP_ENDPOINT=0.0.0.0:4318) or "
                "set DD_LITELLM_USAGE_METRICS_EXPORTER=dogstatsd",
                self.endpoint,
                failure,
            )
        else:
            log.warning(
                "LiteLLM usage metrics: OTLP export to %s failed (%s), kept for the next flush", self.endpoint, failure
            )
        return False


class _DogStatsdSender:
    def __init__(self) -> None:
        url = agent_config.dogstatsd_url
        if url.startswith("/"):
            url = "unix://" + url
        elif "://" not in url:
            url = "udp://" + url
        parsed = parse.urlparse(url)
        self._address: Any
        self._family: Optional[socket.AddressFamily]
        if parsed.scheme == "unix":
            self._family = socket.AF_UNIX
            self._address = parsed.path
            self._max_packet = _UDS_MAX_PACKET
        elif parsed.scheme == "udp":
            # The family is the one of the address the host resolves to when connecting.
            self._family = None
            self._address = (parsed.hostname or "localhost", parsed.port or 8125)
            self._max_packet = _UDP_MAX_PACKET
        else:
            raise ValueError(f"Unknown scheme `{parsed.scheme}` for the DogStatsD URL")
        self._socket: Optional[socket.socket] = None
        # Failed sends so far: each makes the next connection start at the next address the host resolves to.
        self._failed_sends = 0
        self._deadline: Optional[float] = None
        # Set by the writer's close(), so that a send already in progress, which was given no deadline, stops too.
        self._stop_deadline: Optional[float] = None

    def _connect(self) -> socket.socket:
        if self._socket is None:
            if self._family is not None:
                candidates = [(self._family, self._address)]
            else:
                # IPv4 first, as the DogStatsD client does, so that an Agent listening on IPv4 only keeps working on a
                # host that resolves to both; a host that resolves to IPv6 only gets IPv6.
                host, port = self._address
                addresses = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
                addresses.sort(key=lambda address: address[0] != socket.AF_INET)
                candidates = [(family, address) for family, _, _, _, address in addresses]
                if candidates:
                    start = self._failed_sends % len(candidates)
                    candidates = candidates[start:] + candidates[:start]
            for index, (family, address) in enumerate(candidates):
                sock = socket.socket(family, socket.SOCK_DGRAM)
                try:
                    # A full Unix socket buffer blocks a send briefly instead of dropping it; the flush runs on the
                    # writer's own thread.
                    sock.settimeout(_SEND_TIMEOUT_SECONDS)
                    sock.connect(address)
                except OSError:
                    sock.close()
                    if index + 1 == len(candidates):
                        raise
                    continue
                self._socket = sock
                break
            else:
                raise OSError(f"{self._address[0]} resolves to no address")
        return self._socket

    def send(self, lines: list[str], deadline: Optional[float] = None) -> None:
        """Send the lines, stopping when deadline, a time.monotonic() value, passes: a full Unix socket can make each
        packet wait for its send timeout.
        """
        self._deadline = deadline
        self._send_lines([line.encode("utf-8") for line in lines])

    def stop_by(self, deadline: Optional[float]) -> None:
        """Make every send, the one in progress included, stop when deadline, a time.monotonic() value, passes."""
        self._stop_deadline = deadline

    def _past_deadline(self) -> bool:
        now = time.monotonic()
        return any(d is not None and now >= d for d in (self._deadline, self._stop_deadline))

    def _send_lines(self, lines: list[bytes]) -> None:
        packet: list[bytes] = []
        size = 0
        for data in lines:
            if len(data) > _MAX_LINE:
                log.debug(
                    "LiteLLM usage metrics: dropping a %d-byte DogStatsD line, longer than the Agent reads",
                    len(data),
                )
                continue
            if packet and size + 1 + len(data) > self._max_packet:
                self._send_packet(packet)
                packet, size = [], 0
            size += len(data) + (1 if packet else 0)
            packet.append(data)
        if packet:
            self._send_packet(packet)

    def _send_packet(self, packet: list[bytes]) -> None:
        data = b"\n".join(packet)
        for attempt in range(_SEND_ATTEMPTS):
            # Checked before every attempt: a full buffer makes this loop wait between retries.
            if self._past_deadline():
                log.debug("LiteLLM usage metrics: dropping %d DogStatsD lines at exit", len(packet))
                return
            try:
                sock = self._connect()
                # Resolving the host name is not bounded by the socket timeout and can outlast the deadline.
                if self._past_deadline():
                    log.debug("LiteLLM usage metrics: dropping %d DogStatsD lines at exit", len(packet))
                    self.close()
                    return
                sock.send(data)
                return
            except OSError as e:
                if e.errno == errno.EMSGSIZE:
                    if len(packet) > 1:
                        # The socket takes smaller datagrams than assumed (macOS limits Unix datagrams to 2 KiB by
                        # default): send smaller packets from now on.
                        self._max_packet = max(self._max_packet // 2, 512)
                        self._send_lines(packet)
                    else:
                        log.debug(
                            "LiteLLM usage metrics: dropping a %d-byte DogStatsD line, longer than the socket takes",
                            len(data),
                        )
                    return
                if e.errno in (errno.ENOBUFS, errno.EAGAIN) and attempt + 1 < _SEND_ATTEMPTS:
                    # The receiver's buffer is full; macOS reports it at once instead of blocking.
                    time.sleep(0.005)
                    continue
                log.debug("LiteLLM usage metrics: failed to send a DogStatsD packet", exc_info=True)
                # A UDP connect succeeds without a listener, so only a failed send shows that an address is wrong.
                self._failed_sends += 1
                self.close()
                return

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None


class UsageMetricsWriter(ForksafeAwakeablePeriodicService):
    """Collects usage metric observations and exports them every interval seconds."""

    def __init__(self, exporter: str, interval: float, metrics: Optional[list[str]] = None) -> None:
        super().__init__(interval=interval)
        self._exporter = exporter
        self._metric_names = metrics
        self._usage = ai_usage.UsageMetrics(metrics)
        self._window_start_ns = time.time_ns()
        self._flush_lock = forksafe.Lock()
        # Set at exit: a flush in progress stops retrying, so the last flush does not wait behind it.
        self._stopping = forksafe.Event()
        # Set by close(), so that the worker's own last flush as it stops keeps to the same deadline.
        self._shutdown_deadline: Optional[float] = None
        self._otlp: Optional[_OtlpSender] = None
        self._dogstatsd: Optional[_DogStatsdSender] = None
        # OTLP exports that failed in a retryable way, oldest first.
        self._pending: deque[bytes] = deque(maxlen=_MAX_PENDING_EXPORTS)
        if exporter == "dogstatsd":
            self._dogstatsd = _DogStatsdSender()
        else:
            self._otlp = _OtlpSender()

    def record(
        self, profile_id: str, observation: dict[str, Any], deployment_attributes: Optional[dict[str, str]]
    ) -> None:
        """Project and record one observation. Never raises."""
        try:
            issues = self._usage.record(profile_id, observation, deployment_attributes)
        except ValueError as e:
            log.debug("LiteLLM usage metrics: %s observation not recorded: %s", profile_id, e.args)
            _count_codes("usage_metrics.observations_rejected", profile_id, e.args[:1])
            return
        except Exception:
            log.debug("LiteLLM usage metrics: %s observation not recorded", profile_id, exc_info=True)
            _count_codes("usage_metrics.observations_rejected", profile_id, ("internal_error",))
            return
        if issues:
            log.debug("LiteLLM usage metrics: %s observation recorded with issues %s", profile_id, issues)
            _count_codes("usage_metrics.observation_issues", profile_id, issues)

    def periodic(self) -> None:
        self.flush()

    def on_shutdown(self, deadline: Optional[float] = None) -> None:  # type: ignore[override]
        # The process is exiting: stop retrying, then try each pending export once, within a bounded time.
        self._stopping.set()
        self.flush(retry=False, deadline=deadline if deadline is not None else self._shutdown_deadline)

    def close(self) -> None:
        """Stop the worker, flush what is left as at exit, and release the exporter's socket, all within the shutdown
        time.
        """
        deadline = self._shutdown_deadline = time.monotonic() + _SHUTDOWN_SECONDS
        # A flush in progress stops retrying, and a DogStatsD send in progress stops at the deadline; the worker
        # flushes once more as it stops.
        self._stopping.set()
        if self._dogstatsd is not None:
            self._dogstatsd.stop_by(deadline)
        self.stop()
        self.join(max(deadline - time.monotonic(), 0.0))
        self.on_shutdown(deadline)
        if self._dogstatsd is not None:
            self._dogstatsd.close()

    def reset(self) -> None:
        # In a forked child, drop the parent's points and pending exports (the parent exports them), and the
        # parent's connections.
        self._usage = ai_usage.UsageMetrics(self._metric_names)
        self._window_start_ns = time.time_ns()
        self._pending.clear()
        if self._otlp is not None:
            self._otlp = _OtlpSender()
        if self._dogstatsd is not None:
            self._dogstatsd.stop_by(None)
            self._dogstatsd.close()

    def flush(self, retry: bool = True, deadline: Optional[float] = None) -> None:
        # At exit (retry False), the whole flush has the shutdown time, or what deadline leaves of it, waiting for one
        # in progress included.
        if not retry:
            limit = time.monotonic() + _SHUTDOWN_SECONDS
            deadline = limit if deadline is None else min(deadline, limit)
        if not self._flush_lock.acquire(timeout=-1 if deadline is None else max(deadline - time.monotonic(), 0.0)):
            log.debug("LiteLLM usage metrics: a flush in progress outlasted the shutdown time")
            return
        try:
            # A wall clock set back must not end the window before it starts.
            end_ns = max(time.time_ns(), self._window_start_ns)
            start_ns, self._window_start_ns = self._window_start_ns, end_ns
            if self._otlp is not None:
                payload = self._usage.take_otlp(SCOPE_NAME, __version__, start_ns, end_ns)
                if payload:
                    if len(self._pending) == self._pending.maxlen:
                        log.debug("LiteLLM usage metrics: dropping the oldest unsent OTLP export")
                    self._pending.append(payload)
                self._send_pending(self._otlp, deadline)
            elif self._dogstatsd is not None:
                lines = self._usage.take_dogstatsd()
                if lines:
                    self._dogstatsd.send(lines, deadline)
        except Exception:
            log.debug("LiteLLM usage metrics: export failed", exc_info=True)
        finally:
            self._flush_lock.release()

    def _send_pending(self, sender: _OtlpSender, deadline: Optional[float]) -> None:
        """Send the pending exports in order. Normally stop at the first that has to wait for a later flush; at
        exit (with a deadline), try each once, as long as the shutdown time allows, since none can wait.
        """
        while self._pending:
            # A periodic flush that close() interrupted starts no new request: the exit flush, which has the shutdown
            # deadline, sends what is left. A request already in progress cannot be cancelled and ends within its
            # timeout.
            if deadline is None and self._stopping.is_set():
                return
            if deadline is not None and time.monotonic() >= deadline:
                log.debug("LiteLLM usage metrics: dropping %d unsent OTLP exports at exit", len(self._pending))
                self._pending.clear()
                return
            try:
                done = sender.send(self._pending[0], self._stopping, retry=deadline is None, deadline=deadline)
            except Exception:
                # A failure that a retry would not fix, such as an invalid endpoint: drop the export.
                log.debug("LiteLLM usage metrics: OTLP export failed", exc_info=True)
                done = True
            if not done and deadline is None:
                return
            self._pending.popleft()


def _count_codes(metric: str, profile_id: str, codes: Any) -> None:
    """Count rejection or issue codes in instrumentation telemetry. Never raises."""
    try:
        for code in codes:
            telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.TRACERS,
                metric,
                1,
                (("integration_name", "litellm"), ("profile", profile_id), ("code", str(code))),
            )
    except Exception:
        log.debug("LiteLLM usage metrics: failed to count %s", metric, exc_info=True)
