from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import timezone
from email.utils import parsedate_to_datetime
import gzip
import logging
import math
from secrets import SystemRandom
import ssl
from threading import Event
from time import monotonic
from time import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler
from urllib.request import OpenerDirector
from urllib.request import Request
from urllib.request import build_opener
import zlib

from opentelemetry.util.re import parse_env_headers

from ddtrace.internal.http_client import HTTPClient
from ddtrace.internal.settings import env


log = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:4318"
_MAX_RETRIES = 6
_MAX_REQUEST_SIZE = 64 * 1024 * 1024
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_RANDOM = SystemRandom()


class HttpExporter:
    """Send OTLP protobuf payloads without the upstream HTTP transport dependencies."""

    def __init__(
        self,
        signal: str,
        path: str,
        endpoint: str | None = None,
        certificate_file: str | None = None,
        client_key_file: str | None = None,
        client_certificate_file: str | None = None,
        headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None = None,
        timeout: float | None = None,
        compression: Any = None,
        max_request_size: int | None = None,
    ) -> None:
        prefix = f"OTEL_EXPORTER_OTLP_{signal.upper()}"
        self._signal = signal
        self._endpoint = endpoint or _resolve_endpoint(prefix, path)
        self._headers = list(_resolve_headers(headers, f"{prefix}_HEADERS"))
        self._timeout = timeout if timeout is not None else _environment_float(f"{prefix}_TIMEOUT", 10.0)
        self._compression = _resolve_compression(compression, f"{prefix}_COMPRESSION")
        self._max_request_size = _MAX_REQUEST_SIZE if max_request_size is None else max_request_size
        self._shutdown = Event()
        self._client: HTTPClient | None
        self._opener: OpenerDirector | None

        if not _has_header(self._headers, "content-type"):
            self._headers.insert(0, ("Content-Type", "application/x-protobuf"))
        if self._compression != "none" and not _has_header(self._headers, "content-encoding"):
            self._headers.append(("Content-Encoding", self._compression))

        certificate = certificate_file or _environment(f"{prefix}_CERTIFICATE", "OTEL_EXPORTER_OTLP_CERTIFICATE", "")
        client_certificate = client_certificate_file or env.get(f"{prefix}_CLIENT_CERTIFICATE") or ""
        client_key = client_key_file or env.get(f"{prefix}_CLIENT_KEY") or ""
        if certificate or client_certificate or client_key:
            self._client = None
            context = ssl.create_default_context(cafile=certificate or None)
            if client_certificate:
                context.load_cert_chain(client_certificate, client_key or None)
            self._opener = build_opener(HTTPSHandler(context=context))
            self._path = ""
        else:
            parsed = urlsplit(self._endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"Invalid OTLP HTTP endpoint: {self._endpoint!r}")
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            self._path = parsed.path or "/"
            if parsed.query:
                self._path += f"?{parsed.query}"
            self._client = HTTPClient(
                base_url,
                timeout_ms=max(1, int(self._timeout * 1000)),
                headers=self._headers,
                treat_http_errors_as_errors=False,
            )
            self._opener = None

    def _export(self, payload: bytes, success: Any, failure: Any, timeout_millis: float | None) -> Any:
        if self._shutdown.is_set():
            return failure
        if self._max_request_size > 0 and len(payload) > self._max_request_size:
            log.error(
                "Failed to export OpenTelemetry %s: payload size %d exceeds the %d byte limit",
                self._signal,
                len(payload),
                self._max_request_size,
            )
            return failure

        payload = _compress(payload, self._compression)
        timeout = self._timeout if timeout_millis is None else min(self._timeout, timeout_millis / 1000)
        deadline = monotonic() + timeout

        for attempt in range(_MAX_RETRIES):
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            try:
                response_status, retry_after = self._send(payload, remaining)
            except Exception as error:
                retry_after = None
                failure_reason: Any = error
            else:
                if 200 <= response_status < 400:
                    return success
                failure_reason = f"HTTP {response_status}"
                if response_status not in _RETRYABLE_STATUS_CODES:
                    log.error("Failed to export OpenTelemetry %s: %s", self._signal, failure_reason)
                    return failure

            delay = retry_after if retry_after is not None else 2**attempt * _RANDOM.uniform(0.8, 1.2)
            if attempt + 1 == _MAX_RETRIES or delay > deadline - monotonic():
                break
            log.warning(
                "Transient error exporting OpenTelemetry %s (%s); retrying in %.2fs",
                self._signal,
                failure_reason,
                delay,
            )
            if self._shutdown.wait(delay):
                return failure

        log.error("Failed to export OpenTelemetry %s before the deadline", self._signal)
        return failure

    def _send(self, payload: bytes, timeout: float) -> tuple[int, float | None]:
        if self._client is not None:
            native_response = self._client.post(self._path, body=payload, timeout_ms=max(1, int(timeout * 1000)))
            return native_response.status_code, _parse_retry_after(native_response.header("retry-after"))

        request = Request(self._endpoint, data=payload, headers=dict(self._headers), method="POST")
        try:
            opener = self._opener
            if opener is None:
                raise RuntimeError("OTLP HTTP exporter has no configured transport")
            with opener.open(request, timeout=timeout) as url_response:
                return url_response.status, _parse_retry_after(url_response.headers.get("retry-after"))
        except HTTPError as error:
            return error.code, _parse_retry_after(error.headers.get("retry-after"))

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        self._shutdown.set()

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True


def _resolve_endpoint(prefix: str, path: str) -> str:
    signal_endpoint = env.get(f"{prefix}_ENDPOINT")
    if signal_endpoint:
        return signal_endpoint
    return (env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or _DEFAULT_ENDPOINT).rstrip("/") + path


def _resolve_headers(
    headers: Sequence[tuple[str, str]] | Mapping[str, str] | str | None,
    signal_name: str,
) -> tuple[tuple[str, str], ...]:
    configured = headers if headers is not None else _environment(signal_name, "OTEL_EXPORTER_OTLP_HEADERS", "")
    if isinstance(configured, str):
        return tuple(parse_env_headers(configured).items())
    if isinstance(configured, Mapping):
        return tuple(configured.items())
    return tuple(configured)


def _resolve_compression(compression: Any, signal_name: str) -> str:
    configured = compression or _environment(signal_name, "OTEL_EXPORTER_OTLP_COMPRESSION", "none")
    name = str(getattr(configured, "value", configured)).lower()
    if name not in ("none", "gzip", "deflate"):
        raise ValueError(f"Unsupported OTLP HTTP compression: {name!r}")
    return name


def _compress(payload: bytes, compression: str) -> bytes:
    if compression == "gzip":
        return gzip.compress(payload)
    if compression == "deflate":
        return zlib.compress(payload)
    return payload


def _environment(signal_name: str, global_name: str, default: str) -> str:
    return env.get(signal_name) or env.get(global_name) or default


def _environment_float(signal_name: str, default: float) -> float:
    configured = env.get(signal_name) or env.get("OTEL_EXPORTER_OTLP_TIMEOUT")
    return float(configured) if configured is not None else default


def _has_header(headers: Sequence[tuple[str, str]], name: str) -> bool:
    return any(key.lower() == name for key, _ in headers)


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(retry_at.timestamp() - time(), 0.0)
    return max(seconds, 0.0) if math.isfinite(seconds) else None
