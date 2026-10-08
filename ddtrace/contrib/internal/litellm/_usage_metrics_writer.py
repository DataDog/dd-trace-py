"""Periodic export of LiteLLM usage and cost metrics.

Records are projected into metric points by the native ``ai_usage`` module as they arrive. Every
interval the points recorded since the last flush are encoded once and sent, either as an OTLP/HTTP
protobuf ``ExportMetricsServiceRequest`` (delta temporality) or as DogStatsD lines.
"""

from __future__ import annotations

import socket
import time
from typing import Any
from typing import Optional
from urllib import parse

from ddtrace.internal import forksafe
from ddtrace.internal.http_client import HTTPClient
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._opentelemetry import otel_config
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


def _parse_headers(raw: str) -> list[tuple[str, str]]:
    headers = []
    for item in (raw or "").split(","):
        key, sep, value = item.strip().partition("=")
        if sep and key.strip():
            headers.append((key.strip(), parse.unquote(value.strip())))
    return headers


class _OtlpSender:
    def __init__(self) -> None:
        # The HTTP /v1/metrics endpoint: OTEL_EXPORTER_OTLP_METRICS_ENDPOINT as given, else
        # OTEL_EXPORTER_OTLP_ENDPOINT + /v1/metrics, else the agentless intake or the local Agent.
        endpoint = otel_config.exporter.TRACE_METRICS_ENDPOINT
        url = parse.urlsplit(endpoint)
        self.path = url.path or "/v1/metrics"
        self.endpoint = endpoint
        self._client = HTTPClient(
            f"{url.scheme}://{url.netloc}",
            timeout_ms=otel_config.exporter.METRICS_TIMEOUT,
            headers=_parse_headers(otel_config.exporter.METRICS_HEADERS),
            treat_http_errors_as_errors=False,
        )

    def send(self, payload: bytes) -> None:
        response = self._client.post(self.path, headers=[("Content-Type", "application/x-protobuf")], body=payload)
        status = response.status_code
        if status >= 400:
            log.warning("LiteLLM usage metrics: OTLP export to %s failed with status %s", self.endpoint, status)


class _DogStatsdSender:
    def __init__(self) -> None:
        url = agent_config.dogstatsd_url
        if url.startswith("/"):
            url = "unix://" + url
        elif "://" not in url:
            url = "udp://" + url
        parsed = parse.urlparse(url)
        self._address: Any
        if parsed.scheme == "unix":
            self._family = socket.AF_UNIX
            self._address = parsed.path
            self._max_packet = _UDS_MAX_PACKET
        elif parsed.scheme == "udp":
            self._family = socket.AF_INET6 if parsed.hostname and ":" in parsed.hostname else socket.AF_INET
            self._address = (parsed.hostname or "localhost", parsed.port or 8125)
            self._max_packet = _UDP_MAX_PACKET
        else:
            raise ValueError(f"Unknown scheme `{parsed.scheme}` for DogStatsD URL `{url}`")
        self._socket: Optional[socket.socket] = None

    def _connect(self) -> socket.socket:
        if self._socket is None:
            sock = socket.socket(self._family, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.connect(self._address)
            self._socket = sock
        return self._socket

    def send(self, lines: list[str]) -> None:
        packet: list[bytes] = []
        size = 0
        for line in lines:
            data = line.encode("utf-8")
            if packet and size + 1 + len(data) > self._max_packet:
                self._send_packet(b"\n".join(packet))
                packet, size = [], 0
            packet.append(data)
            size += len(data) + (1 if size else 0)
        if packet:
            self._send_packet(b"\n".join(packet))

    def _send_packet(self, packet: bytes) -> None:
        try:
            self._connect().send(packet)
        except OSError:
            log.debug("LiteLLM usage metrics: failed to send a DogStatsD packet", exc_info=True)
            self.close()

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None


class UsageMetricsWriter(ForksafeAwakeablePeriodicService):
    """Collects usage metric observations and exports them every ``interval`` seconds."""

    def __init__(self, exporter: str, interval: float, metrics: Optional[list[str]] = None) -> None:
        super().__init__(interval=interval)
        self._exporter = exporter
        self._metric_names = metrics
        self._metrics = ai_usage.UsageMetrics(metrics)
        self._window_start_ns = time.time_ns()
        self._flush_lock = forksafe.Lock()
        self._otlp: Optional[_OtlpSender] = None
        self._dogstatsd: Optional[_DogStatsdSender] = None
        if exporter == "dogstatsd":
            self._dogstatsd = _DogStatsdSender()
        else:
            self._otlp = _OtlpSender()

    def record(
        self, profile_id: str, observation: dict[str, Any], deployment_attributes: Optional[dict[str, str]]
    ) -> None:
        """Project and record one observation. Never raises."""
        try:
            issues = self._metrics.record(profile_id, observation, deployment_attributes)
        except ValueError as e:
            log.debug("LiteLLM usage metrics: %s observation not recorded: %s", profile_id, e.args)
            return
        except Exception:
            log.debug("LiteLLM usage metrics: %s observation not recorded", profile_id, exc_info=True)
            return
        if issues:
            log.debug("LiteLLM usage metrics: %s observation recorded with issues %s", profile_id, issues)

    def periodic(self) -> None:
        self.flush()

    def on_shutdown(self) -> None:  # type: ignore[override]
        self.flush()

    def reset(self) -> None:
        # In a forked child, drop the parent's points: the parent exports them.
        self._metrics = ai_usage.UsageMetrics(self._metric_names)
        self._window_start_ns = time.time_ns()
        if self._dogstatsd is not None:
            self._dogstatsd.close()

    def flush(self) -> None:
        with self._flush_lock:
            end_ns = time.time_ns()
            start_ns, self._window_start_ns = self._window_start_ns, end_ns
            try:
                if self._otlp is not None:
                    payload = self._metrics.take_otlp(SCOPE_NAME, __version__, start_ns, end_ns)
                    if payload:
                        self._otlp.send(payload)
                elif self._dogstatsd is not None:
                    lines = self._metrics.take_dogstatsd()
                    if lines:
                        self._dogstatsd.send(lines)
            except Exception:
                log.debug("LiteLLM usage metrics: export failed", exc_info=True)
