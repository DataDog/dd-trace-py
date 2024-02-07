from typing import Optional
from urllib.parse import quote

from ddtrace.debugging._config import di_config
from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.internal import compat
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import AwakeablePeriodicService
from ddtrace.internal.runtime import container
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter


log = get_logger(__name__)
meter = metrics.get_meter("uploader")


class LogsIntakeUploaderV1(AwakeablePeriodicService):
    """Logs intake uploader.

    This class implements an interface with the debugger logs intake for both
    the debugger and the events platform.
    """

    ENDPOINT = di_config._intake_endpoint

    RETRY_ATTEMPTS = 3

    def __init__(self, queue: BufferedEncoder, interval: Optional[float] = None) -> None:
        super().__init__(interval or di_config.upload_flush_interval)
        self._queue = queue
        self._headers = {
            "Content-type": "application/json; charset=utf-8",
            "Accept": "text/plain",
        }

        container.update_headers_with_container_info(self._headers, container.get_container_info())

        if di_config._tags_in_qs and di_config.tags:
            self.ENDPOINT += f"?ddtags={quote(di_config.tags)}"
        self._connect = connector(di_config._intake_url, timeout=di_config.upload_timeout)

        # Make it retryable
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
                resp = compat.get_connection_response(conn)
                if not (200 <= resp.status < 300):
                    log.error("Failed to upload payload: [%d] %r", resp.status, resp.read())
                    meter.increment("upload.error", tags={"status": str(resp.status)})
                else:
                    meter.increment("upload.success")
                    meter.distribution("upload.size", len(payload))
        except Exception:
            log.error("Failed to write payload", exc_info=True)
            meter.increment("error")

    def upload(self) -> None:
        """Upload request."""
        self.awake()

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
