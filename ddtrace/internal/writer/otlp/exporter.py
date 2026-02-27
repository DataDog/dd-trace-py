from __future__ import annotations

import random
import time
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection


log = get_logger(__name__)

# Retry on these status codes (transient/server errors)
RETRY_STATUS_CODES = (429, 502, 503, 504)
OTLP_RETRY_ATTEMPTS = 5
OTLP_INITIAL_BACKOFF_SECONDS = 0.1


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter for retry attempt (0-based)."""
    wait = OTLP_INITIAL_BACKOFF_SECONDS * (2**attempt)
    return wait + random.uniform(0, wait * 0.2)  # nosec


def _send_otlp_post(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> Response:
    """Single attempt: POST body to url with given headers and timeout."""
    conn = get_connection(url, timeout=timeout)
    try:
        req_headers = {
            "Content-Type": "application/json",
            **headers,
        }
        # Connection is created with base_path from url (e.g. /v1/traces); request path "" uses it
        conn.request("POST", "", body, req_headers)
        resp = conn.getresponse()
        return Response.from_http_response(resp)
    finally:
        conn.close()


class OTLPHttpTraceExporter:
    """
    Export OTLP trace payloads via HTTP POST with retries.

    Sends JSON (Content-Type: application/json). Uses exponential backoff
    for 429, 502, 503, 504. Does not retry on other 4xx.
    """

    def __init__(
        self,
        endpoint_url: str,
        headers: Optional[dict[str, str]] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        # Use endpoint as-is (config already adds /v1/traces only for default)
        self._endpoint_url = endpoint_url.rstrip("/")
        self._headers = headers or {}
        self._timeout = timeout_seconds

    def export(self, body: bytes) -> Optional[Response]:
        """
        POST body to OTLP traces endpoint with retries.

        :param body: Request body (JSON bytes).
        :returns: Response on success (status < 400), or None after final failure.
        """
        last_response = None
        for attempt in range(OTLP_RETRY_ATTEMPTS):
            try:
                resp = _send_otlp_post(
                    self._endpoint_url,
                    body,
                    self._headers,
                    self._timeout,
                )
                last_response = resp
                if resp.status < 400:
                    log.debug(
                        "OTLP trace export succeeded to %s (attempt %d)",
                        self._endpoint_url,
                        attempt + 1,
                    )
                    return resp
                if resp.status in RETRY_STATUS_CODES:
                    if attempt < OTLP_RETRY_ATTEMPTS - 1:
                        delay = _backoff_delay(attempt)
                        log.debug(
                            "OTLP trace export got %s, retrying in %.3fs (attempt %d/%d)",
                            resp.status,
                            delay,
                            attempt + 1,
                            OTLP_RETRY_ATTEMPTS,
                        )
                        time.sleep(delay)
                        continue
                # Non-retryable or last attempt
                break
            except Exception as e:
                if attempt < OTLP_RETRY_ATTEMPTS - 1:
                    delay = _backoff_delay(attempt)
                    log.debug(
                        "OTLP trace export connection error: %s, retrying in %.3fs (attempt %d/%d)",
                        e,
                        delay,
                        attempt + 1,
                        OTLP_RETRY_ATTEMPTS,
                    )
                    time.sleep(delay)
                else:
                    log.error(
                        "Failed to send OTLP traces to %s after %d attempts: %s. "
                        "See docs.datadoghq.com/tracing/troubleshooting/connection_errors/",
                        self._endpoint_url,
                        OTLP_RETRY_ATTEMPTS,
                        e,
                        exc_info=True,
                        extra={"send_to_telemetry": False},
                    )
                    return None

        if last_response is not None and last_response.status >= 400:
            log.error(
                "Failed to send OTLP traces to %s: HTTP %s %s after %d attempts. "
                "See docs.datadoghq.com/tracing/troubleshooting/connection_errors/",
                self._endpoint_url,
                last_response.status,
                last_response.reason,
                OTLP_RETRY_ATTEMPTS,
                extra={"send_to_telemetry": False},
            )
        return last_response
