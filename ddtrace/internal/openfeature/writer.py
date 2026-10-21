"""
Writer for Feature Flag Exposure events to EVP proxy intake.
"""

import atexit
from collections.abc import Callable
import json
import time
from typing import Any
from typing import Optional
from typing import TypedDict
from typing import Union

from ddtrace import config
from ddtrace.internal.evp_proxy.constants import DEFAULT_EVP_PAYLOAD_SIZE_LIMIT
from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_EVENT_PLATFORM_VALUE
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.logger import get_safe_logger as get_logger
from ddtrace.internal.openfeature._evp_transport import EVPRoute
from ddtrace.internal.openfeature._evp_transport import FeatureFlagEVPRouteSelector
from ddtrace.internal.openfeature._evp_transport import _is_definitive_pre_send_failure
from ddtrace.internal.openfeature._evp_transport import get_evp_connection
from ddtrace.internal.openfeature._evp_transport import get_feature_flag_evp_route_selector
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings.openfeature import REMOTE_CONFIG
from ddtrace.internal.settings.openfeature import config as ffe_config
from ddtrace.internal.threads import RLock
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter


logger = get_logger(__name__)

EXPOSURE_ENDPOINT = "/api/v2/exposures"
EXPOSURE_SUBDOMAIN_NAME = EVP_SUBDOMAIN_HEADER_EVENT_PLATFORM_VALUE

# Buffer and payload limits
BUFFER_LIMIT = 1000
PAYLOAD_SIZE_LIMIT = DEFAULT_EVP_PAYLOAD_SIZE_LIMIT

# Default configuration
DEFAULT_INTERVAL = 1.0
DEFAULT_TIMEOUT = 2.0


class _RequiredExposureEvent(TypedDict):
    timestamp: int
    allocation: dict[str, str]
    flag: dict[str, str]
    variant: dict[str, str]
    subject: dict[str, Any]


class ExposureEvent(_RequiredExposureEvent, total=False):
    """
    Feature flag exposure event structure.
    """

    serial_id: int


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
    exposures: list[ExposureEvent]


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
        route_selector: Optional[FeatureFlagEVPRouteSelector] = None,
        connection_factory: Optional[Callable[..., Any]] = None,
        retry_wait: Optional[Callable[[float], Any]] = None,
    ) -> None:
        # Read configuration from settings
        if enabled is None:
            enabled = ffe_config.ffe_intake_enabled

        if interval is None:
            interval = ffe_config.ffe_intake_heartbeat_interval

        super().__init__(interval=interval)
        self._lock = RLock()
        self._buffer: list[ExposureEvent] = []
        self._buffer_size: int = 0
        self._in_flight_count = 0
        self._shutdown_complete = False
        self._timeout: float = timeout
        self._enabled: bool = enabled
        self._route_selector = route_selector or get_feature_flag_evp_route_selector()
        self._connection_factory = connection_factory
        self._retry_wait = retry_wait

        # Configure intake endpoint
        self._intake: str = agent_config.trace_agent_url
        self._endpoint: str = f"{EVP_PROXY_AGENT_BASE_PATH}{EXPOSURE_ENDPOINT}"

        # Configure headers
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            EVP_SUBDOMAIN_HEADER_NAME: EXPOSURE_SUBDOMAIN_NAME,
        }

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

        self._shutdown_complete = False
        super().start()
        logger.debug("started ExposureWriter to %s", self._url)
        atexit.register(self.on_shutdown)

    def stop(self, timeout=None):
        if not self._enabled:
            return

        super().stop(timeout=timeout)
        budget = DEFAULT_TIMEOUT if timeout is None else timeout
        self.join(timeout=budget)
        if not self._shutdown_complete:
            logger.debug(
                "Feature Flags: exposure shutdown wait timed out after %.1fs: %d queued event(s) not flushed, "
                "%d in-flight event(s) with delivery unknown; background work may finish after provider shutdown",
                budget,
                len(self._buffer),
                self._in_flight_count,
            )
        atexit.unregister(self.on_shutdown)

    def on_shutdown(self):
        with self._lock:
            pending = len(self._buffer)
        logger.debug("Feature Flags: flushing %d pending exposure event(s) before shutdown", pending)
        count, outcome = self._flush()
        logger.debug("Feature Flags: exposure writer shutdown flush outcome: %d event(s) %s", count, outcome)
        self._shutdown_complete = True

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
            # Queued, not delivered: the periodic flush reports the delivery outcome.
            logger.debug(
                "enqueued exposure event for %s, buffer size: %d", event.get("flag", {}).get("key"), len(self._buffer)
            )

    def periodic(self) -> None:
        """
        Periodically flush buffered events to the intake.
        """
        self._flush()

    def _flush(self) -> tuple[int, str]:
        """Drain one batch and report what happened for shutdown diagnostics."""
        with self._lock:
            if not self._buffer:
                return 0, "had no pending work"
            events = self._buffer
            self._buffer = []
            self._buffer_size = 0
            self._in_flight_count += len(events)

        try:
            logger.debug("Feature Flags: flushing %d exposure event(s)", len(events))
            payload = self._encode(events)
            if not payload:
                return len(events), "dropped because encoding failed"
            route = self._route_selector.select()
            if route is None:
                logger.debug(
                    "Feature Flags: dropping %d exposure event(s): no event delivery route is available", len(events)
                )
                return len(events), "dropped because no delivery route was available"
            response = self._send_payload_with_retry(payload, len(events), route)
            status = getattr(response, "status", None)
            if isinstance(status, int) and status >= 300:
                logger.debug(
                    "Feature Flags: %d exposure event(s) not delivered (HTTP %d); dropping them", len(events), status
                )
                return len(events), "dropped after HTTP %d" % status
            if isinstance(status, int):
                return len(events), "accepted by the selected route (HTTP %d)" % status
            return len(events), "sent with no HTTP status available"
        except Exception:
            logger.debug(
                "failed to send %d exposure events to %s; dropping them", len(events), self._intake, exc_info=True
            )
            return len(events), "dropped after a delivery error"
        finally:
            with self._lock:
                self._in_flight_count -= len(events)

    def _send_payload_with_retry(self, payload: bytes, num_events: int, route: EVPRoute) -> Response:
        if self._route_selector.configuration_source != REMOTE_CONFIG:
            return self._send_payload(payload, num_events, route)

        def wait_before_retry(delay):
            if delay:
                logger.debug("Feature Flags: exposure request failed before sending; retrying in %.3fs", delay)
            (self._retry_wait or time.sleep)(delay)

        # AIDEV-NOTE: Keep Remote Config's bounded retries only when no bytes were
        # sent. A later ambiguous failure must stop retries even after a refusal;
        # Agentless batches retain the selector's separate fallback policy.
        send = fibonacci_backoff_with_jitter(
            attempts=self.RETRY_ATTEMPTS,
            initial_wait=0.618 * self._interval / (1.618**self.RETRY_ATTEMPTS) / 2,
            until=lambda result: not (isinstance(result, Exception) and _is_definitive_pre_send_failure(result)),
            sleep_func=wait_before_retry,
        )(self._send_payload)
        response: Union[Response, Exception] = send(payload, num_events, route)
        # The retry helper returns terminal exceptions accepted by until.
        if isinstance(response, Exception):
            raise response
        return response

    def _encode(self, events: list[ExposureEvent]) -> bytes:
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
            return encoded
        except (TypeError, ValueError):
            logger.debug("failed to encode %d exposure events", len(events), exc_info=True)
            return b""

    def _send_payload(self, payload: bytes, num_events: int, route: EVPRoute) -> Response:
        """
        Send payload through one immutable route snapshot.
        """

        def send_once(active_route: EVPRoute) -> Response:
            endpoint = active_route.endpoint(EXPOSURE_ENDPOINT)
            headers = dict(active_route.headers)
            connection_factory = self._connection_factory or get_connection
            conn = get_evp_connection(active_route, self._timeout, connection_factory)
            try:
                conn.request("POST", endpoint, payload, headers)
                resp = conn.getresponse()
                # Only the status determines delivery. Draining a direct response body
                # can block the synchronous shutdown flush: stdlib socket timeouts are
                # not a total deadline when the response keeps making progress.
                response = Response(status=resp.status)
                if response.status >= 300:
                    logger.debug(
                        "failed to send %d exposure events to %s%s, got response code %d",
                        num_events,
                        active_route.intake,
                        endpoint,
                        response.status,
                    )
                else:
                    logger.debug(
                        "sent %d exposure events to %s%s (HTTP %d; %s)",
                        num_events,
                        active_route.intake,
                        endpoint,
                        response.status,
                        "intake accepted"
                        if active_route.direct
                        else "relay accepted; downstream delivery is not observed",
                    )
                return response
            except Exception:
                logger.debug("failed to send %d exposure events", num_events, exc_info=True)
                raise
            finally:
                conn.close()

        return self._route_selector.send(route, send_once)

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
