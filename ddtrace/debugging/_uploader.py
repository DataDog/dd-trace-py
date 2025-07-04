from enum import Enum
from typing import Any
from typing import Optional
from typing import Set
from urllib.parse import quote

from ddtrace.debugging._config import di_config
from ddtrace.debugging._encoding import LogSignalJsonEncoder
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._signal.collector import SignalCollector
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


class LogsIntakeUploaderV1(ForksafeAwakeablePeriodicService):
    """Logs intake uploader.

    This class implements an interface with the debugger logs intake for both
    the debugger and the events platform.
    """

    _instance: Optional["LogsIntakeUploaderV1"] = None
    _products: Set[UploaderProduct] = set()

    __queue__ = SignalQueue
    __collector__ = SignalCollector

    ENDPOINT = di_config._intake_endpoint

    RETRY_ATTEMPTS = 3

    def __init__(self, interval: Optional[float] = None) -> None:
        super().__init__(interval if interval is not None else di_config.upload_interval_seconds)

        self._queue = self.__queue__(encoder=LogSignalJsonEncoder(di_config.service_name), on_full=self._on_buffer_full)
        self._collector = self.__collector__(self._queue)
        self._headers = {
            "Content-type": "application/json; charset=utf-8",
            "Accept": "text/plain",
        }

        if di_config._tags_in_qs and di_config.tags:
            self.ENDPOINT += f"?ddtags={quote(di_config.tags)}"
        self._connect = connector(di_config._intake_url, timeout=di_config.upload_timeout)

        # Make it retry-able
        self._write_with_backoff = fibonacci_backoff_with_jitter(
            initial_wait=0.618 * self.interval / (1.618**self.RETRY_ATTEMPTS) / 2,
            attempts=self.RETRY_ATTEMPTS,
        )(self._write)

        log.debug(
            "Logs intake uploader initialized (url: %s, endpoint: %s, interval: %f)",
            di_config._intake_url,
            self.ENDPOINT,
            self.interval,
        )

    def _write(self, payload: bytes) -> None:
        try:
            with self._connect() as conn:
                conn.request(
                    "POST",
                    self.ENDPOINT,
                    payload,
                    headers=self._headers,
                )
                resp = conn.getresponse()
                if not (200 <= resp.status < 300):
                    log.error("Failed to upload payload: [%d] %r", resp.status, resp.read())
                    meter.increment("upload.error", tags={"status": str(resp.status)})
                else:
                    meter.increment("upload.success")
                    meter.distribution("upload.size", len(payload))
        except Exception:
            log.error("Failed to write payload", exc_info=True)
            meter.increment("error")

    def _on_buffer_full(self, _item: Any, _encoded: bytes) -> None:
        self.upload()

    def upload(self) -> None:
        """Upload request."""
        self.awake()

    def reset(self) -> None:
        """Reset the buffer on fork."""
        self._queue = self.__queue__(encoder=self._queue._encoder, on_full=self._on_buffer_full)
        self._collector._encoder = self._queue

    def periodic(self) -> None:
        """Upload the buffer content to the logs intake."""
        count = self._queue.count
        if count:
            payload = self._queue.flush()
            if payload is not None:
                try:
                    self._write_with_backoff(payload)
                    meter.distribution("batch.cardinality", count)
                except Exception:
                    log.debug("Cannot upload logs payload", exc_info=True)

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
