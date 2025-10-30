"""
Writer for Feature Flag Exposure events to EVP proxy intake.
"""

import atexit
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import TypedDict

from ddtrace import config
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings.openfeature import config as ffe_config


logger = get_logger(__name__)

EVP_PROXY_AGENT_BASE_PATH = "/evp_proxy/v2"
EXPOSURE_ENDPOINT = "/api/v2/exposures"
EVP_SUBDOMAIN_HEADER_NAME = "X-Datadog-EVP-Subdomain"
EXPOSURE_SUBDOMAIN_NAME = "event-platform-intake"

# Buffer and payload limits
BUFFER_LIMIT = 1000
PAYLOAD_SIZE_LIMIT = 5 << 20  # 5MB

# Default configuration
DEFAULT_INTERVAL = 1.0
DEFAULT_TIMEOUT = 2.0


class ExposureEvent(TypedDict):
    """
    Feature flag exposure event structure.
    """

    timestamp: int
    allocation: Dict[str, str]
    flag: Dict[str, str]
    variant: Dict[str, str]
    subject: Dict[str, Any]


class GeoContext(TypedDict, total=False):
    """
    Geographic context information.
    """

    country_iso_code: str
    country: str


class Context(TypedDict, total=False):
    """
    Context information for batched exposures.
    """

    geo: GeoContext
    service: str
    version: str
    env: str


class BatchedExposures(TypedDict, total=False):
    """
    Batched exposure events with context.
    """

    context: Context
    exposures: List[ExposureEvent]


class ExposureWriter(PeriodicService):
    """
    Async writer for feature flag exposure events.

    Sends exposure events to the Datadog Agent's EVP proxy endpoint at
    /evp_proxy/v2/api/v2/exposures
    """

    RETRY_ATTEMPTS = 3

    def __init__(
        self,
        interval: Optional[float] = None,
        timeout: float = DEFAULT_TIMEOUT,
        enabled: Optional[bool] = None,
    ) -> None:
        # Read configuration from settings
        if enabled is None:
            enabled = ffe_config.ffe_intake_enabled

        if interval is None:
            interval = ffe_config.ffe_intake_heartbeat_interval

        super(ExposureWriter, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer: List[ExposureEvent] = []
        self._buffer_size: int = 0
        self._timeout: float = timeout
        self._enabled: bool = enabled

        # Configure intake endpoint
        self._intake: str = agent_config.trace_agent_url
        self._endpoint: str = f"{EVP_PROXY_AGENT_BASE_PATH}{EXPOSURE_ENDPOINT}"

        # Configure headers
        self._headers: Dict[str, str] = {
            "Content-Type": "application/json",
            EVP_SUBDOMAIN_HEADER_NAME: EXPOSURE_SUBDOMAIN_NAME,
        }

        # Setup retry mechanism
        self._send_payload_with_retry = fibonacci_backoff_with_jitter(
            attempts=self.RETRY_ATTEMPTS,
            initial_wait=0.618 * self._interval / (1.618**self.RETRY_ATTEMPTS) / 2,
            until=lambda result: isinstance(result, Response),
        )(self._send_payload)

        logger.debug(
            "ExposureWriter initialized with intake=%s, endpoint=%s, enabled=%s, interval=%s",
            self._intake,
            self._endpoint,
            self._enabled,
            interval,
        )

    def start(self, *args, **kwargs):
        if not self._enabled:
            logger.debug("ExposureWriter disabled, not starting")
            return

        super(ExposureWriter, self).start()
        logger.debug("started ExposureWriter to %s", self._url)
        atexit.register(self.on_shutdown)

    def stop(self, timeout=None):
        if not self._enabled:
            return

        super(ExposureWriter, self).stop(timeout=timeout)
        logger.debug("stopped ExposureWriter to %s", self._url)
        atexit.unregister(self.on_shutdown)

    def on_shutdown(self):
        self.periodic()

    def enqueue(self, event: ExposureEvent) -> None:
        """
        Enqueue an exposure event to be sent to the intake.
        """
        if not self._enabled:
            logger.debug("ExposureWriter disabled, not enqueueing event")
            return

        event_size = len(json.dumps(event).encode("utf-8"))
        with self._lock:
            if len(self._buffer) >= BUFFER_LIMIT:
                logger.debug("ExposureWriter event buffer full (limit is %d), dropping event", BUFFER_LIMIT)
                return

            if self._buffer_size + event_size > PAYLOAD_SIZE_LIMIT:
                logger.debug("manually flushing buffer because queueing next event will exceed payload limit")
                self.periodic()

            self._buffer.append(event)
            self._buffer_size += event_size
            logger.debug("enqueued exposure event, buffer size: %d", len(self._buffer))

    def periodic(self) -> None:
        """
        Periodically flush buffered events to the intake.
        """
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []
            self._buffer_size = 0

        payload = self._encode(events)
        if not payload:
            return

        try:
            self._send_payload_with_retry(payload, len(events))
        except Exception:
            logger.debug("failed to send %d exposure events to %s", len(events), self._intake, exc_info=True)

    def _encode(self, events: List[ExposureEvent]) -> bytes:
        """
        Encode events to JSON bytes wrapped in batch structure with context.
        """
        try:
            # Build context from global config
            context: Context = {}
            if config.service:
                context["service"] = config.service
            if config.env:
                context["env"] = config.env
            if config.version:
                context["version"] = config.version

            # Build batched payload
            batched: BatchedExposures = {"exposures": events}
            if context:
                batched["context"] = context

            encoded = json.dumps(batched).encode("utf-8")
            logger.debug("encoded %d exposure events to be sent", len(events))
            return encoded
        except (TypeError, ValueError):
            logger.debug("failed to encode %d exposure events", len(events), exc_info=True)
            return b""

    def _send_payload(self, payload: bytes, num_events: int):
        """
        Send payload to the EVP proxy intake endpoint.
        """
        conn = get_connection(self._intake)
        try:
            conn.request("POST", self._endpoint, payload, self._headers)
            resp = conn.getresponse()
            if resp.status >= 300:
                logger.debug(
                    "failed to send %d exposure events to %s, got response code %d, status: %s",
                    num_events,
                    self._url,
                    resp.status,
                    resp.read(),
                )
            else:
                logger.debug("sent %d exposure events to %s", num_events, self._url)
            return Response.from_http_response(resp)
        except Exception:
            logger.debug("failed to send %d exposure events to %s", num_events, self._intake, exc_info=True)
            raise
        finally:
            conn.close()

    @property
    def _url(self) -> str:
        """
        Full URL for the exposure intake endpoint.
        """
        return f"{self._intake}{self._endpoint}"


# Global singleton writer instance
_EXPOSURE_WRITER: Optional[ExposureWriter] = None


def get_exposure_writer() -> ExposureWriter:
    """
    Get or create the global exposure writer instance.
    """
    global _EXPOSURE_WRITER
    if _EXPOSURE_WRITER is None:
        _EXPOSURE_WRITER = ExposureWriter()
    return _EXPOSURE_WRITER


def start_exposure_writer():
    """
    Start the global exposure writer instance.
    """
    writer = get_exposure_writer()
    writer.start()


def stop_exposure_writer():
    """
    Stop the global exposure writer instance.
    """
    global _EXPOSURE_WRITER
    if _EXPOSURE_WRITER is not None:
        _EXPOSURE_WRITER.stop()
        _EXPOSURE_WRITER = None
