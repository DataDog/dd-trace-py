from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Optional
from typing import Set
from urllib.parse import quote

from ddtrace.debugging._config import di_config
from ddtrace.debugging._encoding import LogSignalJsonEncoder
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._encoding import SnapshotJsonEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.internal import agent
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import ForksafeAwakeablePeriodicService
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter


log = get_logger(__name__)
meter = metrics.get_meter("uploader")


class UploaderProduct(str, Enum):
    """Uploader products."""

    DEBUGGER = "dynamic_instrumentation"
    EXCEPTION_REPLAY = "exception_replay"
    CODE_ORIGIN_SPAN = "code_origin.span"


@dataclass
class UploaderTrack:
    endpoint: str
    queue: SignalQueue


class LogsIntakeUploaderV1(ForksafeAwakeablePeriodicService):
    """Logs intake uploader.

    This class implements an interface with the debugger logs intake for both
    the debugger and the events platform.
    """

    _instance: Optional["LogsIntakeUploaderV1"] = None
    _products: Set[UploaderProduct] = set()

    __queue__ = SignalQueue
    __collector__ = SignalCollector

    RETRY_ATTEMPTS = 3

    def __init__(self, interval: Optional[float] = None) -> None:
        super().__init__(interval if interval is not None else di_config.upload_interval_seconds)

        endpoint_suffix = f"?ddtags={quote(di_config.tags)}" if di_config._tags_in_qs and di_config.tags else ""
        agent_endpoints = set(agent_info.get("endpoints", [])) if (agent_info := agent.info()) is not None else set()

        self._tracks = {
            SignalTrack.LOGS: UploaderTrack(
                endpoint=f"/debugger/v1/input{endpoint_suffix}",
                queue=self.__queue__(
                    encoder=LogSignalJsonEncoder(di_config.service_name), on_full=self._on_buffer_full
                ),
            ),
            SignalTrack.SNAPSHOT: UploaderTrack(
                endpoint=(
                    f"/debugger/v2/input{endpoint_suffix}"
                    if "/debugger/v2/input" in agent_endpoints
                    else f"/debugger/v1/diagnostics{endpoint_suffix}"
                ),
                queue=self.__queue__(encoder=SnapshotJsonEncoder(di_config.service_name), on_full=self._on_buffer_full),
            ),
        }
        self._collector = self.__collector__({t: ut.queue for t, ut in self._tracks.items()})
        self._headers = {
            "Content-type": "application/json; charset=utf-8",
            "Accept": "text/plain",
        }

        self._connect = connector(di_config._intake_url, timeout=di_config.upload_timeout)

        # Make it retry-able
        self._write_with_backoff = fibonacci_backoff_with_jitter(
            initial_wait=0.618 * self.interval / (1.618**self.RETRY_ATTEMPTS) / 2,
            attempts=self.RETRY_ATTEMPTS,
        )(self._write)

        log.debug(
            "Logs intake uploader initialized (url: %s, endpoints: %s, interval: %f)",
            di_config._intake_url,
            {t: ut.endpoint for t, ut in self._tracks.items()},
            self.interval,
        )

        self._flush_full = False

    def _write(self, payload: bytes, endpoint: str) -> None:
        try:
            with self._connect() as conn:
                conn.request("POST", endpoint, payload, headers=self._headers)
                resp = conn.getresponse()
                if not (200 <= resp.status < 300):
                    log.error("Failed to upload payload to endpoint %s: [%d] %r", endpoint, resp.status, resp.read())
                    meter.increment("upload.error", tags={"status": str(resp.status)})
                else:
                    meter.increment("upload.success")
                    meter.distribution("upload.size", len(payload))
        except Exception:
            log.error("Failed to write payload to endpoint %s", endpoint, exc_info=True)
            meter.increment("error")

    def _on_buffer_full(self, _item: Any, _encoded: bytes) -> None:
        self._flush_full = True
        self.upload()

    def upload(self) -> None:
        """Upload request."""
        self.awake()

    def reset(self) -> None:
        """Reset the buffer on fork."""
        for track in self._tracks.values():
            track.queue = self.__queue__(encoder=track.queue._encoder, on_full=self._on_buffer_full)
        self._collector._tracks = {t: ut.queue for t, ut in self._tracks.items()}

    def _flush_track(self, track: UploaderTrack) -> None:
        queue = track.queue
        payload = queue.flush()
        if payload is not None:
            try:
                self._write_with_backoff(payload, track.endpoint)
                meter.distribution("batch.cardinality", queue.count)
            except Exception:
                log.debug("Cannot upload logs payload", exc_info=True)

    def periodic(self) -> None:
        """Upload the buffer content to the logs intake."""
        if self._flush_full:
            # We received the signal to flush a full buffer
            self._flush_full = False
            for track in self._tracks.values():
                if track.queue.is_full():
                    self._flush_track(track)

        for track in self._tracks.values():
            if track.queue.count:
                self._flush_track(track)

    on_shutdown = periodic

    @classmethod
    def get_collector(cls) -> SignalCollector:
        if cls._instance is None:
            raise RuntimeError("No products registered with the uploader")

        return cls._instance._collector

    @classmethod
    def register(cls, product: UploaderProduct) -> None:
        if product in cls._products:
            return

        cls._products.add(product)

        if cls._instance is None:
            cls._instance = cls()
            cls._instance.start()

    @classmethod
    def unregister(cls, product: UploaderProduct) -> None:
        if product not in cls._products:
            return

        cls._products.remove(product)

        if not cls._products and cls._instance is not None:
            cls._instance.stop()
            cls._instance = None
